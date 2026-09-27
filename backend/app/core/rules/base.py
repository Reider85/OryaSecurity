from dataclasses import dataclass, field


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
