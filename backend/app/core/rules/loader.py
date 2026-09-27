from __future__ import annotations

import re
import hashlib
from typing import Dict, List, Any
import yaml

from app.core.rules.base import RuleMatch


def _hash_value(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def load_pii_rules(rules_path: str) -> List[Dict[str, Any]]:
    """Load PII rules from YAML file."""
    try:
        with open(rules_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            return data.get('rules', [])
    except (FileNotFoundError, yaml.YAMLError):
        # Fallback to empty list if YAML file is missing or invalid
        return []


def _compile_pattern(pattern: str) -> re.Pattern:
    """Compile regex pattern with error handling."""
    try:
        return re.compile(pattern)
    except re.error:
        # Return pattern that never matches if compilation fails
        return re.compile(r"(?!)")


def match_pii_with_rules(text: str, rules: List[Dict[str, Any]]) -> List[RuleMatch]:
    """Match PII using loaded YAML rules."""
    matches: List[RuleMatch] = []
    
    for rule in rules:
        if rule.get('type') != 'regex':
            continue
            
        pattern_str = rule.get('pattern', '')
        if not pattern_str:
            continue
            
        rule_id = rule.get('id', 'unknown')
        rule_name = rule.get('name', 'Unknown Rule')
        severity = rule.get('severity', 'medium')
        action = rule.get('action', 'block')
        
        pattern = _compile_pattern(pattern_str)
        
        for m in pattern.finditer(text):
            matches.append(
                RuleMatch(
                    rule_id=rule_id,
                    rule_name=rule_name,
                    value_hash=_hash_value(m.group()),
                    position=(m.start(), m.end()),
                    severity=severity,
                    action=action,
                )
            )
    
    return matches


def get_pii_rules(rules_path: str = "rules/pii.yaml") -> List[Dict[str, Any]]:
    """Get PII rules from YAML, fallback to hardcoded if needed."""
    rules = load_pii_rules(rules_path)
    
    # If no rules loaded from YAML, return hardcoded defaults
    if not rules:
        return [
            {
                "id": "pii_ssn_us",
                "name": "US Social Security Number",
                "type": "regex",
                "pattern": r"\b\d{3}-\d{2}-\d{4}\b",
                "severity": "high",
                "action": "block",
                "version": "1.0.0"
            },
            {
                "id": "pii_email",
                "name": "Email Address",
                "type": "regex",
                "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
                "severity": "medium",
                "action": "block",
                "version": "1.0.0"
            },
            {
                "id": "pii_passport_ru",
                "name": "Russian Passport Number",
                "type": "regex",
                "pattern": r"\b\d{4}\s?\d{6}\b",
                "severity": "high",
                "action": "block",
                "version": "1.0.0"
            }
        ]
    
    return rules


def check_pii_with_yaml(text: str, rules_path: str = "rules/pii.yaml") -> List[RuleMatch]:
    """Check for PII using YAML-configured rules."""
    rules = get_pii_rules(rules_path)
    return match_pii_with_rules(text, rules)