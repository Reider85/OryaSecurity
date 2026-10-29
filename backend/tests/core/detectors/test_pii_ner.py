"""Unit tests for the Presidio NER provider and detector factory (EP-13/Prompt 35).

Engine calls are faked with duck-typed objects (``analyze`` returning
``RecognizerResult``-like instances) so the unit tests run without the optional
``ner`` extra; the real-engine behaviour is covered by
:class:`TestRealEngine` at the bottom (skipped when presidio is not installed).
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.core.detectors.pii_ner import PresidioNerProvider, make_detector
from app.core.redaction.presidio_config import (
    ENTITY_MEDICAL_CONDITION,
    ENTITY_RU_INN,
    ENTITY_RU_PASSPORT,
    build_presidio_engine,
    is_valid_inn,
)
from app.core.rules.base import RuleMatch, _hash_value


class FakeNerResult:
    """RecognizerResult-like duck type."""

    def __init__(self, entity_type: str, start: int, end: int, score: float = 0.9):
        self.entity_type = entity_type
        self.start = start
        self.end = end
        self.score = score


class FakePresidioEngine:
    def __init__(self, results=(), error: Exception | None = None):
        self.results = list(results)
        self.error = error
        self.calls: list[dict] = []

    def analyze(self, text=None, language=None, score_threshold=None, **kwargs):
        self.calls.append(
            {"text": text, "language": language, "score_threshold": score_threshold}
        )
        if self.error is not None:
            raise self.error
        return list(self.results)


class TestIsValidInn:
    def test_valid_10_digit(self):
        assert is_valid_inn("7707083893")

    def test_valid_12_digit(self):
        assert is_valid_inn("770708389324")
        assert is_valid_inn("500100732259")

    def test_bad_checksum_10_digit(self):
        assert not is_valid_inn("1234567890")

    def test_bad_checksum_12_digit(self):
        assert not is_valid_inn("123456789012")

    def test_wrong_length(self):
        assert not is_valid_inn("12345")

    def test_non_digits(self):
        assert not is_valid_inn("770708389A")


class TestPresidioNerProvider:
    def test_passport_mapped_to_rule_match(self):
        text = "passport 1234 567890"
        engine = FakePresidioEngine([FakeNerResult(ENTITY_RU_PASSPORT, 9, 20, 0.85)])
        matches = PresidioNerProvider(engine)(text)
        assert len(matches) == 1
        match = matches[0]
        assert match.rule_id == "pii_passport_ru"
        assert match.rule_name == "Russian Passport Number"
        assert match.severity == "high"
        assert match.action == "block"
        assert match.position == (9, 20)
        assert match.value_hash == _hash_value("1234 567890")

    def test_valid_inn_kept(self):
        text = "ИНН 7707083893 организации"
        engine = FakePresidioEngine([FakeNerResult(ENTITY_RU_INN, 4, 14, 0.55)])
        matches = PresidioNerProvider(engine)(text)
        assert [m.rule_id for m in matches] == ["pii_inn_ru"]

    def test_inn_with_bad_checksum_dropped(self):
        text = "ИНН 1234567890 подделка"
        engine = FakePresidioEngine([FakeNerResult(ENTITY_RU_INN, 4, 14, 0.55)])
        assert PresidioNerProvider(engine)(text) == []

    def test_medical_condition_mapped(self):
        text = "patient diagnosed with diabetes"
        engine = FakePresidioEngine(
            [FakeNerResult(ENTITY_MEDICAL_CONDITION, 21, 29, 0.75)]
        )
        matches = PresidioNerProvider(engine)(text)
        assert len(matches) == 1
        assert matches[0].rule_id == "pii_medical_condition"
        assert matches[0].severity == "medium"
        assert matches[0].action == "log_only"

    def test_low_score_dropped(self):
        engine = FakePresidioEngine([FakeNerResult(ENTITY_RU_PASSPORT, 0, 11, 0.1)])
        assert PresidioNerProvider(engine)("passport 1234 567890") == []

    def test_unknown_entity_ignored(self):
        engine = FakePresidioEngine([FakeNerResult("US_SSN", 5, 16, 0.95)])
        assert PresidioNerProvider(engine)("SSN: 123-45-6789") == []

    def test_engine_error_returns_empty(self):
        engine = FakePresidioEngine(error=RuntimeError("presidio down"))
        assert PresidioNerProvider(engine)("anything") == []

    def test_none_engine_returns_empty(self):
        assert PresidioNerProvider(None)("anything") == []

    def test_empty_text_returns_empty(self):
        engine = FakePresidioEngine([FakeNerResult(ENTITY_RU_PASSPORT, 0, 0)])
        assert PresidioNerProvider(engine)("") == []

    def test_invalid_span_skipped(self):
        engine = FakePresidioEngine([FakeNerResult(ENTITY_RU_PASSPORT, 5, 5, 0.9)])
        assert PresidioNerProvider(engine)("passport 1234 567890") == []

    def test_results_ordered_by_entity_priority(self):
        # ИНН is more specific than a passport-shaped digit run -> listed first
        # so ``_merge_matches`` keeps it when the two spans overlap.
        engine = FakePresidioEngine(
            [
                FakeNerResult(ENTITY_RU_PASSPORT, 4, 14, 0.7),
                FakeNerResult(ENTITY_RU_INN, 4, 14, 0.55),
            ]
        )
        matches = PresidioNerProvider(engine)("ИНН 7707083893")
        assert [m.rule_id for m in matches] == ["pii_inn_ru", "pii_passport_ru"]

    def test_language_and_threshold_forwarded_to_engine(self):
        engine = FakePresidioEngine([])
        provider = PresidioNerProvider(engine)
        provider("text")
        assert engine.calls[0]["language"] == settings.presidio_language
        assert engine.calls[0]["score_threshold"] == settings.presidio_min_score


class TestMakeDetector:
    def test_disabled_returns_regex_only(self):
        detector = make_detector(enabled=False)
        assert detector.ner is None
        matches = detector.detect("SSN: 123-45-6789")
        assert any(m.rule_id == "pii_ssn_us" for m in matches)

    def test_enabled_combines_regex_and_ner(self):
        text = "patient HIV status unknown"
        engine = FakePresidioEngine(
            [FakeNerResult(ENTITY_MEDICAL_CONDITION, 8, 11, 0.9)]
        )
        detector = make_detector(presidio_engine=engine, enabled=True)
        matches = detector.detect(text)
        assert any(m.rule_id == "pii_medical_condition" for m in matches)
        assert text[8:11] == "HIV"

    def test_inn_supersedes_weak_phone_regex(self):
        # The generic phone rule (log_only) matches any digit run and would
        # otherwise swallow the ИНН detection; a blocking NER hit must win.
        text = "телефон 7707083893"
        start = text.index("7707083893")
        engine = FakePresidioEngine(
            [FakeNerResult(ENTITY_RU_INN, start, start + 10, 0.55)]
        )
        detector = make_detector(presidio_engine=engine, enabled=True)
        matches = detector.detect(text)
        rule_ids = [m.rule_id for m in matches]
        assert "pii_inn_ru" in rule_ids
        assert "pii_phone" not in rule_ids

    def test_passport_span_reported_once(self):
        # Whether the span comes from the YAML rule or from NER, downstream
        # consumers must see exactly one pii_passport_ru match.
        text = "passport 1234 567890"
        engine = FakePresidioEngine([FakeNerResult(ENTITY_RU_PASSPORT, 9, 20, 0.85)])
        detector = make_detector(presidio_engine=engine, enabled=True)
        span_matches = [m for m in detector.detect(text) if m.position == (9, 20)]
        assert len(span_matches) == 1
        assert span_matches[0].rule_id == "pii_passport_ru"

    def test_ner_exception_never_breaks_detection(self):
        # PIIDetector swallows provider errors; regex path must still work.
        engine = FakePresidioEngine(error=RuntimeError("engine exploded"))
        detector = make_detector(presidio_engine=engine, enabled=True)
        matches = detector.detect("SSN: 123-45-6789")
        assert any(m.rule_id == "pii_ssn_us" for m in matches)


class TestRealEngine:
    """End-to-end against the real Presidio engine (requires the `ner` extra)."""

    def test_detects_passport_with_space(self, real_provider):
        text = "паспорт 1234 567890"
        matches = real_provider(text)
        assert any(m.rule_id == "pii_passport_ru" for m in matches)

    def test_detects_current_passport_form(self, real_provider):
        text = "серия 71 1234567"
        matches = real_provider(text)
        assert any(m.rule_id == "pii_passport_ru" for m in matches)

    def test_detects_valid_inn_rejects_invalid(self, real_provider):
        valid = real_provider("мой инн 7707083893")
        assert any(m.rule_id == "pii_inn_ru" for m in valid)
        assert not any(
            m.rule_id == "pii_inn_ru" for m in real_provider("мой инн 1234567890")
        )

    def test_medical_terms_case_insensitive(self, real_provider):
        matches = real_provider("У пациента диагноз Диабет")
        assert any(m.rule_id == "pii_medical_condition" for m in matches)

    def test_clean_text_no_hits(self, real_provider):
        assert real_provider("Обычный текст о погоде") == []


@pytest.fixture(scope="module")
def real_provider() -> PresidioNerProvider:
    pytest.importorskip("presidio_analyzer")
    engine = build_presidio_engine()
    assert engine is not None, "engine must build when presidio is installed"
    return PresidioNerProvider(engine)
