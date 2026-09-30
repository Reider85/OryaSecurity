from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
import yaml
import jsonschema
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.core.rules.base import Rule, RuleMatch, _hash_value


class RuleValidationError(ValueError):
    """Raised when rule data fails schema or regex validation."""


class RuleNotFoundError(ValueError):
    """Raised when a rule id does not exist."""


class RuleExistsError(ValueError):
    """Raised when creating a rule with a duplicate id."""


class RuleFileHandler(FileSystemEventHandler):
    """Handler for rule file changes."""
    
    def __init__(self, rule_set: 'RuleSet'):
        self.rule_set = rule_set
        self._last_reload_time = time.time()
    
    def on_modified(self, event):
        """Handle file modification events."""
        if event.is_directory:
            return
        
        file_path = Path(event.src_path)
        if file_path.suffix == '.yaml' and file_path.parent == self.rule_set.rules_dir:
            print(f"Rule file modified: {file_path}")
            # Schedule reload to avoid concurrent access issues
            asyncio.create_task(self.rule_set.reload_rules())


class RuleSet:
    """Manages loading, validation, and matching of security rules from YAML files."""
    
    def __init__(self, rules_dir: str = "rules"):
        self.rules_dir = Path(rules_dir)
        self.rules: List[Rule] = []
        self.rules_by_id: Dict[str, Rule] = {}
        self.last_loaded_time: float = 0
        self.schema_path = Path(__file__).parent / "schema.json"
        self.observer: Optional[Observer] = None
        
        # Load rules on initialization
        self._load_rules()
    
    def start_hot_reload(self):
        """Start watching rule files for changes."""
        if self.observer is not None:
            return
        
        self.observer = Observer()
        event_handler = RuleFileHandler(self)
        self.observer.schedule(event_handler, str(self.rules_dir), recursive=False)
        self.observer.start()
    
    def stop_hot_reload(self):
        """Stop watching rule files."""
        if self.observer is not None:
            self.observer.stop()
            self.observer.join()
            self.observer = None
    
    async def reload_rules(self):
        """Reload rules from YAML files."""
        old_rules = self.rules.copy()
        old_rules_by_id = self.rules_by_id.copy()
        
        try:
            self._load_rules()
            print(f"Rules reloaded successfully. Loaded {len(self.rules)} rules.")
        except Exception as e:
            print(f"Failed to reload rules: {e}")
            # Keep old rules on failure
            self.rules = old_rules
            self.rules_by_id = old_rules_by_id
    
    def _load_rules(self):
        """Load rules from all YAML files in the rules directory."""
        self.rules = []
        self.rules_by_id = {}
        
        if not self.rules_dir.exists():
            print(f"Rules directory not found: {self.rules_dir}")
            return
        
        # Load schema
        schema = self._load_schema()
        
        # Load all YAML files
        yaml_files = list(self.rules_dir.glob("*.yaml"))
        for yaml_file in yaml_files:
            try:
                rules_data = self._load_yaml_file(yaml_file)
                if rules_data:
                    file_rules = self._parse_rules(rules_data, schema, str(yaml_file))
                    self.rules.extend(file_rules)
                    for rule in file_rules:
                        self.rules_by_id[rule.id] = rule
            except Exception as e:
                print(f"Failed to load rules from {yaml_file}: {e}")
        
        self.last_loaded_time = time.time()
    
    def _load_schema(self) -> Dict:
        """Load JSON schema for rule validation."""
        try:
            with open(self.schema_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"Schema file not found: {self.schema_path}")
            return {}
        except json.JSONDecodeError as e:
            print(f"Invalid JSON schema: {e}")
            return {}
    
    def _load_yaml_file(self, file_path: Path) -> Dict:
        """Load YAML file and return rules data."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                if data and 'rules' in data:
                    return data
        except (FileNotFoundError, yaml.YAMLError) as e:
            print(f"Error loading YAML file {file_path}: {e}")
        
        return {}
    
    def _parse_rules(self, rules_data: Dict, schema: Dict, source_file: str) -> List[Rule]:
        """Parse rules data and validate against schema."""
        rules = []
        
        if not rules_data or 'rules' not in rules_data:
            return rules
        
        # Validate entire file against schema (not per-rule)
        if schema:
            try:
                jsonschema.validate(rules_data, schema)
            except jsonschema.ValidationError as e:
                print(f"Invalid rules file {source_file}: {e.message}")
                return rules
        
        for rule_data in rules_data['rules']:
            # Create Rule object
            try:
                rule = Rule(
                    id=rule_data['id'],
                    name=rule_data['name'],
                    type=rule_data['type'],
                    pattern=rule_data['pattern'],
                    severity=rule_data['severity'],
                    action=rule_data['action'],
                    version=rule_data['version'],
                    description=rule_data.get('description'),
                    enabled=rule_data.get('enabled', True)
                )
                rules.append(rule)
            except Exception as e:
                print(f"Failed to create rule from data: {rule_data.get('id', 'unknown')}: {e}")
        
        return rules
    
    def match(self, text: str) -> List[RuleMatch]:
        """Match text against all enabled rules."""
        matches: List[RuleMatch] = []
        
        for rule in self.rules:
            if not rule.enabled or rule.compiled_pattern is None:
                continue
            
            for match_obj in rule.compiled_pattern.finditer(text):
                matches.append(
                    RuleMatch(
                        rule_id=rule.id,
                        rule_name=rule.name,
                        value_hash=_hash_value(match_obj.group()),
                        position=(match_obj.start(), match_obj.end()),
                        severity=rule.severity,
                        action=rule.action,
                    )
                )
        
        return matches
    
    def get_rule(self, rule_id: str) -> Optional[Rule]:
        """Get a specific rule by ID."""
        return self.rules_by_id.get(rule_id)
    
    def get_rules_by_type(self, rule_type: str) -> List[Rule]:
        """Get all rules of a specific type."""
        return [rule for rule in self.rules if rule.type == rule_type and rule.enabled]
    
    def get_rules_by_severity(self, severity: str) -> List[Rule]:
        """Get all rules of a specific severity."""
        return [rule for rule in self.rules if rule.severity == severity and rule.enabled]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about loaded rules."""
        return {
            "total_rules": len(self.rules),
            "enabled_rules": len([r for r in self.rules if r.enabled]),
            "disabled_rules": len([r for r in self.rules if not r.enabled]),
            "rules_by_type": {
                rule_type: len(self.get_rules_by_type(rule_type))
                for rule_type in set(rule.type for rule in self.rules)
            },
            "rules_by_severity": {
                severity: len(self.get_rules_by_severity(severity))
                for severity in ["low", "medium", "high", "critical"]
            },
            "last_loaded_time": self.last_loaded_time,
            "rules_by_id": {rule_id: rule.name for rule_id, rule in self.rules_by_id.items()}
        }
    
    def reload_rule(self, rule_id: str) -> bool:
        """Reload a specific rule by re-parsing its source file."""
        rule = self.get_rule(rule_id)
        if not rule:
            return False
        
        # Find the source file for this rule
        source_file = None
        yaml_files = list(self.rules_dir.glob("*.yaml"))
        for yaml_file in yaml_files:
            try:
                rules_data = self._load_yaml_file(yaml_file)
                if rules_data and 'rules' in rules_data:
                    for rule_data in rules_data['rules']:
                        if rule_data.get('id') == rule_id:
                            source_file = yaml_file
                            break
                if source_file:
                    break
            except Exception:
                continue
        
        if not source_file:
            return False
        
        # Reload rules from the specific file
        try:
            rules_data = self._load_yaml_file(source_file)
            if rules_data:
                schema = self._load_schema()
                file_rules = self._parse_rules(rules_data, schema, str(source_file))
                
                # Update the specific rule
                for new_rule in file_rules:
                    if new_rule.id == rule_id:
                        # Update the rule in our lists
                        for i, existing_rule in enumerate(self.rules):
                            if existing_rule.id == rule_id:
                                self.rules[i] = new_rule
                                break
                        
                        self.rules_by_id[rule_id] = new_rule
                        print(f"Rule {rule_id} reloaded successfully.")
                        return True
        except Exception as e:
            print(f"Failed to reload rule {rule_id}: {e}")

        return False

    def find_rule_source(self, rule_id: str) -> Optional[Path]:
        """Find the YAML file that defines the given rule id."""
        for yaml_file in sorted(self.rules_dir.glob("*.yaml")):
            try:
                rules_data = self._load_yaml_file(yaml_file)
            except Exception:
                continue
            if not rules_data or "rules" not in rules_data:
                continue
            for rule_data in rules_data["rules"]:
                if rule_data.get("id") == rule_id:
                    return yaml_file
        return None

    def list_rules(self) -> List[tuple[Rule, str]]:
        """Return all rules paired with their source file name."""
        result: List[tuple[Rule, str]] = []
        for rule in self.rules:
            source = self.find_rule_source(rule.id)
            source_name = source.name if source else "unknown"
            result.append((rule, source_name))
        return result

    @staticmethod
    def _clean_rule_data(rule_data: Dict[str, Any]) -> Dict[str, Any]:
        """Drop None-valued optional fields before schema validation/persistence."""
        return {k: v for k, v in rule_data.items() if v is not None}

    def _validate_rule_data(self, rule_data: Dict[str, Any]) -> None:
        """Validate a single rule dict against the JSON schema and regex."""
        if not isinstance(rule_data, dict):
            raise RuleValidationError("Rule must be a mapping")

        rule_data = self._clean_rule_data(rule_data)

        required = ["id", "name", "type", "pattern", "severity", "action", "version"]
        missing = [field for field in required if field not in rule_data]
        if missing:
            raise RuleValidationError(f"Missing required fields: {', '.join(missing)}")

        schema = self._load_schema()
        if schema:
            try:
                jsonschema.validate({"rules": [rule_data]}, schema)
            except jsonschema.ValidationError as e:
                raise RuleValidationError(e.message) from e

        if rule_data.get("type") == "regex":
            try:
                re.compile(rule_data["pattern"])
            except re.error as e:
                raise RuleValidationError(f"Invalid regex pattern: {e}") from e

    def _rule_from_data(self, rule_data: Dict[str, Any]) -> Rule:
        """Build a Rule object from validated rule data."""
        return Rule(
            id=rule_data["id"],
            name=rule_data["name"],
            type=rule_data["type"],
            pattern=rule_data["pattern"],
            severity=rule_data["severity"],
            action=rule_data["action"],
            version=rule_data["version"],
            description=rule_data.get("description"),
            enabled=rule_data.get("enabled", True),
        )

    def _write_rules_file(self, file_path: Path, rules_data: List[Dict[str, Any]]) -> None:
        """Persist a list of rule dicts to a YAML file."""
        payload = {"rules": rules_data}
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                payload,
                f,
                sort_keys=False,
                default_flow_style=False,
                allow_unicode=True,
                width=100,
            )

    def save_rule(self, rule_id: str, rule_data: Dict[str, Any]) -> Rule:
        """Update an existing rule in its source YAML file and reload."""
        source_file = self.find_rule_source(rule_id)
        if source_file is None:
            raise RuleNotFoundError(f"Rule '{rule_id}' not found")

        rule_data = self._clean_rule_data(rule_data)
        rules_data = self._load_yaml_file(source_file) or {}
        file_rules = rules_data.get("rules", [])

        updated_entry: Optional[Dict[str, Any]] = None
        new_file_rules: List[Dict[str, Any]] = []
        for entry in file_rules:
            if entry.get("id") == rule_id:
                updated_entry = dict(rule_data)
                new_file_rules.append(updated_entry)
            else:
                new_file_rules.append(entry)

        if updated_entry is None:
            raise RuleNotFoundError(f"Rule '{rule_id}' not found in {source_file.name}")

        self._validate_rule_data(updated_entry)
        self._write_rules_file(source_file, new_file_rules)
        self._load_rules()

        saved = self.get_rule(updated_entry["id"])
        if saved is None:
            raise RuleValidationError(f"Rule '{updated_entry['id']}' failed to load after save")
        return saved

    def create_rule(self, rule_data: Dict[str, Any], source_file: str) -> Rule:
        """Create a new rule in the given YAML file and reload."""
        rule_data = self._clean_rule_data(rule_data)
        self._validate_rule_data(rule_data)

        rule_id = rule_data["id"]
        if rule_id in self.rules_by_id:
            raise RuleExistsError(f"Rule '{rule_id}' already exists")

        # Only allow plain file names inside rules_dir (no path traversal)
        file_name = Path(source_file).name
        if not file_name.endswith(".yaml") or file_name != source_file:
            raise RuleValidationError("source_file must be a .yaml file name inside the rules directory")

        target = self.rules_dir / file_name
        existing = self._load_yaml_file(target) or {}
        file_rules = list(existing.get("rules", []))

        if any(entry.get("id") == rule_id for entry in file_rules):
            raise RuleExistsError(f"Rule '{rule_id}' already exists in {file_name}")

        file_rules.append(dict(rule_data))
        self._write_rules_file(target, file_rules)
        self._load_rules()

        created = self.get_rule(rule_id)
        if created is None:
            raise RuleValidationError(f"Rule '{rule_id}' failed to load after creation")
        return created

    @staticmethod
    def rule_to_dict(rule: Rule) -> Dict[str, Any]:
        """Serialize a Rule to a plain dict (API response / YAML source)."""
        data: Dict[str, Any] = {
            "id": rule.id,
            "name": rule.name,
            "type": rule.type,
            "pattern": rule.pattern,
            "severity": rule.severity,
            "action": rule.action,
            "version": rule.version,
        }
        if rule.description is not None:
            data["description"] = rule.description
        data["enabled"] = rule.enabled
        return data