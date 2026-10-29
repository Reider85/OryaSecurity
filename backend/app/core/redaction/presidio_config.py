"""Presidio engine configuration for non-standard PII (EP-13/Prompt 35, ADR-0007).

Builds a Microsoft Presidio ``AnalyzerEngine`` with three custom recognizers:

* ``RU_PASSPORT`` — Russian passport number: legacy 10-digit (``1234 567890``),
  current 9-digit (``71 1234567``) and letter-series (``АБ1234567``) forms;
* ``RU_INN`` — Russian tax ID (ИНН), 10 or 12 digits; the check-sum is
  validated by the NER provider (see ``app.core.detectors.pii_ner``);
* ``MEDICAL_CONDITION`` — keyword deny-list of Russian/English medical terms —
  exactly the kind of non-standard PII that static regex rules cannot express.

The engine runs on Presidio's ``NoOpNlpEngine``: every recognizer here is
self-contained (patterns/deny-list), so no spaCy language model is required —
the optional ``ner`` extra pulls in ``presidio-analyzer`` only.

Fail-soft contract: when the ``ner`` extra is not installed or the engine
cannot be built, builders return ``None`` and PII detection degrades to
regex-only (the hook in :class:`app.core.detectors.pii.PIIDetector` treats a
missing/raising NER provider as "no NER matches").

The engine instance is a process-wide singleton (``AnalyzerEngine`` holds
loaded recognizers and is safe to share); build it once via
:func:`get_or_build_presidio_engine`.
"""

from __future__ import annotations

import importlib.util
import structlog
from typing import Any, Optional

from app.config import settings

logger = structlog.get_logger(__name__)

# Availability is checked with find_spec, NOT by importing: presidio-analyzer
# (and its dependency tree) is slow to import and must only load when an
# engine is actually built. The real import happens inside the builders.
try:  # pragma: no cover - trivial
    PRESIDIO_AVAILABLE = importlib.util.find_spec("presidio_analyzer") is not None
except (ImportError, ValueError):  # pragma: no cover - broken install
    PRESIDIO_AVAILABLE = False

# Entity types produced by the custom recognizers. RU_PASSPORT intentionally
# shares its rule id with the YAML regex rule (``pii_passport_ru``): whether a
# passport span is found by regex or by NER, downstream consumers (PDP, audit,
# charts) see the same identifier.
ENTITY_RU_PASSPORT = "RU_PASSPORT"
ENTITY_RU_INN = "RU_INN"
ENTITY_MEDICAL_CONDITION = "MEDICAL_CONDITION"

# Russian passport number forms.
_PASSPORT_PATTERNS = (
    ("passport_legacy", r"(?<!\d)\d{4}\s\d{6}(?!\d)", 0.85),  # 1234 567890
    ("passport_legacy_tight", r"(?<!\d)\d{4}\d{6}(?!\d)", 0.70),  # 1234567890
    ("passport_current", r"(?<!\d)\d{2}\s\d{7}(?!\d)", 0.75),  # 71 1234567
    ("passport_series", r"(?<![0-9А-ЯA-Z])[А-ЯЁA-Z]{2}\s?\d{7}(?!\d)", 0.75),  # АБ1234567
)

# Russian ИНН forms (check-sum validation happens in the NER provider, so a
# low pattern score keeps pure digit-runs from over-triggering).
_INN_PATTERNS = (
    ("inn_10", r"(?<!\d)\d{10}(?!\d)", 0.55),
    ("inn_12", r"(?<!\d)\d{12}(?!\d)", 0.55),
)

# Keyword deny-list for medical conditions. Long stems use prefix matching so
# Russian inflections ("диабет", "диабете") still hit; short/ambiguous stems
# ("рак" vs "ракета") are token-bounded. Matching is case-insensitive because
# the recognizer registry compiles patterns with ``regex.I`` by default.
_MEDICAL_TERMS_PREFIX = (
    "диабет",
    "онколог",
    "депресс",
    "шизофрен",
    "эпилепс",
    "туберкул",
    "гепатит",
    "инсульт",
    "инфаркт",
    "гипертон",
    "альцгеймер",
    "лейкоз",
    "лимфом",
    "меланом",
    "цирроз",
    "diabetes",
    "depression",
    "schizophrenia",
    "epilepsy",
    "tuberculosis",
    "hepatitis",
    "alzheimer",
    "oncology",
    "leukemia",
    "asthma",
)
_MEDICAL_TERMS_TOKEN = (
    "рак",
    "вич",
    "сепсис",
    "хобл",
    "hiv",
    "cancer",
    "stroke",
)


def _medical_regex() -> str:
    prefix = "|".join(term for term in _MEDICAL_TERMS_PREFIX)
    token = "|".join(term for term in _MEDICAL_TERMS_TOKEN)
    return rf"(?:\b(?:{prefix})|\b(?:{token})\b)"


def is_valid_inn(value: str) -> bool:
    """Check the Russian ИНН check-sum (digits only, 10 or 12 chars).

    Works without presidio installed so callers can validate spans regardless
    of which engine produced them.
    """
    if not value.isdigit():
        return False

    digits = [int(ch) for ch in value]
    if len(digits) == 10:
        coefficients = (2, 4, 10, 3, 5, 9, 4, 6, 8)
        check = sum(d * c for d, c in zip(digits, coefficients)) % 11 % 10
        return check == digits[9]
    if len(digits) == 12:
        # FNS rules: n11 over the first 10 digits, n12 over the first 11.
        eleventh = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        twelfth = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
        check11 = sum(d * c for d, c in zip(digits[:10], eleventh)) % 11 % 10
        check12 = sum(d * c for d, c in zip(digits[:11], twelfth)) % 11 % 10
        return check11 == digits[10] and check12 == digits[11]
    return False


def _build_passport_recognizer(language: str) -> Any:
    from presidio_analyzer import Pattern, PatternRecognizer

    patterns = [
        Pattern(name=name, regex=regex, score=score)
        for name, regex, score in _PASSPORT_PATTERNS
    ]
    return PatternRecognizer(
        supported_entity=ENTITY_RU_PASSPORT,
        name="ru_passport_recognizer",
        supported_language=language,
        patterns=patterns,
    )


def _build_inn_recognizer(language: str) -> Any:
    from presidio_analyzer import Pattern, PatternRecognizer

    patterns = [
        Pattern(name=name, regex=regex, score=score)
        for name, regex, score in _INN_PATTERNS
    ]
    return PatternRecognizer(
        supported_entity=ENTITY_RU_INN,
        name="ru_inn_recognizer",
        supported_language=language,
        patterns=patterns,
    )


def _build_medical_recognizer(language: str) -> Any:
    from presidio_analyzer import Pattern, PatternRecognizer

    pattern = Pattern(name="medical_terms", regex=_medical_regex(), score=0.75)
    return PatternRecognizer(
        supported_entity=ENTITY_MEDICAL_CONDITION,
        name="medical_condition_recognizer",
        supported_language=language,
        patterns=[pattern],
    )


def build_presidio_engine(language: Optional[str] = None) -> Optional[Any]:
    """Build the Presidio analyzer with the scanner's custom recognizers.

    Returns ``None`` when the optional ``ner`` extra is not installed or the
    engine cannot be constructed (callers degrade to regex-only detection).
    """
    if not PRESIDIO_AVAILABLE:  # pragma: no cover - exercised only without the extra
        logger.debug("presidio_unavailable", hint='pip install -e ".[ner]"')
        return None

    lang = language or settings.presidio_language
    try:
        from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
        from presidio_analyzer.nlp_engine import NoOpNlpEngine

        registry = RecognizerRegistry(
            recognizers=[
                _build_passport_recognizer(lang),
                _build_inn_recognizer(lang),
                _build_medical_recognizer(lang),
            ],
            supported_languages=[lang],
        )
        # Pattern/deny-list recognizers need no linguistic artifacts: the
        # no-op engine avoids shipping a spaCy model just for keyword matching.
        nlp_engine = NoOpNlpEngine(models=[{"lang_code": lang, "model_name": "noop"}])
        nlp_engine.load()
        engine = AnalyzerEngine(
            registry=registry,
            nlp_engine=nlp_engine,
            supported_languages=[lang],
        )
        logger.info("presidio_engine_built", language=lang, recognizers=3)
        return engine
    except Exception:  # noqa: BLE001 - never let engine setup break the app
        logger.warning("presidio_engine_build_failed", exc_info=True)
        return None


_shared_engine: Optional[Any] = None
_shared_engine_built = False


def get_shared_presidio_engine() -> Optional[Any]:
    """Return the cached engine (``None`` if not built yet)."""
    return _shared_engine


def set_shared_presidio_engine(engine: Optional[Any]) -> None:
    """Inject a prebuilt engine (tests, ``app.main`` startup)."""
    global _shared_engine, _shared_engine_built
    _shared_engine = engine
    _shared_engine_built = True


def get_or_build_presidio_engine() -> Optional[Any]:
    """Lazily build and cache the shared engine (negative results cached too)."""
    global _shared_engine, _shared_engine_built
    if not _shared_engine_built:
        _shared_engine = build_presidio_engine()
        _shared_engine_built = True
    return _shared_engine
