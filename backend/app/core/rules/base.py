import re
import hashlib
from dataclasses import dataclass, field
from typing import Optional, Pattern


@dataclass(frozen=True)
class Rule:
    """Represents a single rule loaded from YAML."""
    id: str
    name: str
    type: str
    pattern: str
    severity: str
    action: str
    version: str
    description: Optional[str] = None
    enabled: bool = True
    
    def __post_init__(self):
        """Compile regex pattern after initialization."""
        if self.enabled and self.type == "regex":
            object.__setattr__(self, '_compiled_pattern', re.compile(self.pattern))
        else:
            object.__setattr__(self, '_compiled_pattern', None)
    
    @property
    def compiled_pattern(self) -> Optional[Pattern]:
        """Get compiled regex pattern if enabled and type is regex."""
        return self._compiled_pattern


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    rule_name: str
    value_hash: str
    position: tuple[int, int]
    severity: str
    action: str

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "value_hash": self.value_hash,
            "position": list(self.position),
            "severity": self.severity,
            "action": self.action,
        }


@dataclass(frozen=True)
class Verdict:
    action: str
    reason: str
    rules_matched: list[RuleMatch] = field(default_factory=list)

    @property
    def is_block(self) -> bool:
        return self.action == "block"


def _hash_value(value: str) -> str:
    """Hash a value for privacy (SHA256 truncated to 16 chars)."""
    return hashlib.sha256(value.encode()).hexdigest()[:16]
