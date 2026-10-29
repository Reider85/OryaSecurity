"""Unit tests for PIIDetector (EP-13/Prompt 32): regex detection via the
global YAML RuleSet plus the optional NER hook (Presidio lands in Prompt 35)."""

from __future__ import annotations

from app.core.detectors.pii import PIIDetector, _merge_matches
from app.core.rules.base import RuleMatch


def _match(rule_id: str, start: int, end: int) -> RuleMatch:
    return RuleMatch(
        rule_id=rule_id,
        rule_name=rule_id,
        value_hash="h",
        position=(start, end),
        severity="medium",
        action="block",
    )


class TestRegexDetection:
    def test_detects_ssn(self):
        detector = PIIDetector()
        matches = detector.detect("SSN: 123-45-6789")
        assert any(m.rule_id == "pii_ssn_us" for m in matches)
        ssn = next(m for m in matches if m.rule_id == "pii_ssn_us")
        assert ssn.position == (5, 16)

    def test_detects_email(self):
        detector = PIIDetector()
        matches = detector.detect("write to test@example.com please")
        assert any(m.rule_id == "pii_email" for m in matches)

    def test_detects_passport_ru(self):
        detector = PIIDetector()
        matches = detector.detect("passport 1234 567890")
        assert any(m.rule_id == "pii_passport_ru" for m in matches)

    def test_detects_aws_key(self):
        detector = PIIDetector()
        matches = detector.detect("Key: AKIAIOSFODNN7EXAMPLE")
        assert any(m.rule_id == "secret_aws_key" for m in matches)

    def test_clean_text_no_matches(self):
        detector = PIIDetector()
        assert detector.detect("Just a normal sentence about weather.") == []

    def test_empty_text(self):
        detector = PIIDetector()
        assert detector.detect("") == []

    def test_positions_are_correct(self):
        text = "mail me at test@example.com ok"
        detector = PIIDetector()
        email = next(m for m in detector.detect(text) if m.rule_id == "pii_email")
        assert text[email.position[0] : email.position[1]] == "test@example.com"


class TestNerHook:
    def test_ner_match_is_merged(self):
        def ner(text: str) -> list[RuleMatch]:
            return [_match("pii_custom_medical", 6, 12)]

        detector = PIIDetector(ner=ner)
        matches = detector.detect("patient HIV status unknown")
        assert any(m.rule_id == "pii_custom_medical" for m in matches)

    def test_ner_overlap_with_regex_is_dropped(self):
        # NER claims a span already covered by the SSN regex -> regex wins.
        def ner(text: str) -> list[RuleMatch]:
            return [_match("pii_custom_name", 5, 14)]

        detector = PIIDetector(ner=ner)
        matches = detector.detect("SSN: 123-45-6789")
        assert not any(m.rule_id == "pii_custom_name" for m in matches)
        assert any(m.rule_id == "pii_ssn_us" for m in matches)

    def test_ner_exception_never_breaks_detection(self):
        def ner(text: str) -> list[RuleMatch]:
            raise RuntimeError("presidio down")

        detector = PIIDetector(ner=ner)
        matches = detector.detect("SSN: 123-45-6789")
        assert any(m.rule_id == "pii_ssn_us" for m in matches)

    def test_ner_only_no_regex(self):
        def ner(text: str) -> list[RuleMatch]:
            return [_match("pii_custom_address", 0, 5)]

        detector = PIIDetector(ner=ner)
        matches = detector.detect("hello")
        assert [m.rule_id for m in matches] == ["pii_custom_address"]


class TestMergeMatches:
    def test_disjoint_ner_kept_and_sorted(self):
        regex = [_match("pii_a", 0, 3)]
        ner = [_match("pii_b", 10, 15)]
        merged = _merge_matches(regex, ner)
        assert [m.rule_id for m in merged] == ["pii_a", "pii_b"]

    def test_overlapping_ner_pairs_deduped(self):
        regex: list[RuleMatch] = []
        ner = [_match("pii_b", 0, 5), _match("pii_c", 3, 8)]
        merged = _merge_matches(regex, ner)
        assert [m.rule_id for m in merged] == ["pii_b"]


class TestMergeStrongNerSupersedesHeuristicRegex:
    """Blocking NER hits supersede digit-run heuristic regex hits (Prompt 35):
    a valid ИНН must not be downgraded to ``pii_phone``/``pii_passport_ru``."""

    @staticmethod
    def _regex(rule_id: str, start: int, end: int, action: str, severity: str) -> RuleMatch:
        return RuleMatch(
            rule_id=rule_id,
            rule_name=rule_id,
            value_hash="h",
            position=(start, end),
            severity=severity,
            action=action,
        )

    def test_blocking_ner_replaces_phone_heuristic(self):
        weak = self._regex("pii_phone", 5, 15, "log_only", "low")
        ner = [_match("pii_inn_ru", 5, 15)]
        merged = _merge_matches([weak], ner)
        assert [m.rule_id for m in merged] == ["pii_inn_ru"]

    def test_blocking_ner_supersedes_passport_heuristic(self):
        # No-space 10-digit number: the passport rule and the generic phone
        # rule both match the run; a checksum-validated ИНН wins over both.
        passport = self._regex("pii_passport_ru", 0, 10, "block", "high")
        phone = self._regex("pii_phone", 0, 10, "log_only", "low")
        ner = [_match("pii_inn_ru", 0, 10)]
        merged = _merge_matches([passport, phone], ner)
        assert [m.rule_id for m in merged] == ["pii_inn_ru"]

    def test_blocking_ner_drops_only_overlapping_heuristics(self):
        weak = self._regex("pii_phone", 5, 15, "log_only", "low")
        other = self._regex("pii_email", 40, 55, "block", "medium")
        ner = [_match("pii_inn_ru", 5, 15)]
        merged = _merge_matches([weak, other], ner)
        assert [m.rule_id for m in merged] == ["pii_inn_ru", "pii_email"]

    def test_blocking_ner_loses_to_specific_regex(self):
        strong = self._regex("pii_ssn_us", 5, 16, "block", "high")
        ner = [_match("pii_custom_name", 5, 14)]
        merged = _merge_matches([strong], ner)
        assert [m.rule_id for m in merged] == ["pii_ssn_us"]

    def test_blocking_ner_loses_when_any_overlap_is_specific(self):
        heuristic = self._regex("pii_phone", 0, 10, "log_only", "low")
        specific = self._regex("pii_email", 8, 20, "block", "medium")
        ner = [_match("pii_inn_ru", 5, 12)]
        merged = _merge_matches([heuristic, specific], ner)
        assert [m.rule_id for m in merged] == ["pii_phone", "pii_email"]

    def test_log_only_ner_never_replaces_regex(self):
        weak = self._regex("pii_phone", 5, 15, "log_only", "low")
        ner = [
            RuleMatch(
                rule_id="pii_medical_condition",
                rule_name="medical",
                value_hash="h",
                position=(5, 15),
                severity="medium",
                action="log_only",
            )
        ]
        merged = _merge_matches([weak], ner)
        assert [m.rule_id for m in merged] == ["pii_phone"]
