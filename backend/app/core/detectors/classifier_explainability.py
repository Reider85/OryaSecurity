"""
SHAP explainability for ML classifier decisions (ALPHA Prompt 19).

For a classified prompt the module returns the top-N tokens that drove the
decision as ``{token, shap_value, contribution}`` dicts, ready for storage in
the audit log's ``explainability`` column.

Two explainer engines are supported:

* ``transformers`` — ``shap.Explainer`` over a HuggingFace
  ``text-classification`` pipeline, used when torch and loadable HF weights
  are present;
* ``onnx`` — ``shap.Explainer`` over a callable wrapper around the ONNX
  ``ClassifierModel`` (the production engine, no torch required).

Results are cached in-memory per ``(model_version, text_hash)`` so a prompt
is recomputed only when the model artifact changes. Everything degrades to
``None`` (fail-open) — explainability never breaks the request path.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from app.config import settings
from app.core.logging import get_logger
from app.core.metrics import (
    scanner_explainability_duration_seconds,
    scanner_explainability_total,
)

logger = get_logger(__name__)

# Fallback class order (must match training and ml_classifier.CLASS_NAMES).
CLASS_NAMES = ["benign", "prompt_injection", "jailbreak", "suspicious"]


def _outcome(name: str) -> None:
    scanner_explainability_total.labels(outcome=name).inc()


class ClassifierExplainer:
    """SHAP-based explainability for :class:`PromptClassifier` decisions."""

    def __init__(
        self,
        classifier: Any = None,
        *,
        enabled: Optional[bool] = None,
        top_k: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        max_length: Optional[int] = None,
        cache_max_entries: Optional[int] = None,
    ):
        """
        Args:
            classifier: PromptClassifier instance; the shared singleton is
                resolved lazily when omitted.
            enabled: Master switch (defaults to settings.explainability_enabled).
            top_k: Number of tokens to return (defaults to top 5).
            timeout_seconds: Budget for a single SHAP computation.
            max_length: Token budget fed to the explainer (longer prompts are
                truncated before explaining to bound latency).
            cache_max_entries: LRU capacity for the in-memory cache.
        """
        self._classifier = classifier
        self.enabled = (
            settings.explainability_enabled if enabled is None else bool(enabled)
        )
        self.top_k = settings.explainability_top_k if top_k is None else int(top_k)
        self.timeout_seconds = (
            settings.explainability_timeout_seconds
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        self.max_length = (
            settings.explainability_max_length if max_length is None else int(max_length)
        )
        self.cache_max_entries = (
            settings.explainability_cache_max_entries
            if cache_max_entries is None
            else int(cache_max_entries)
        )

        self._explainer: Any = None
        self._engine: Optional[str] = None
        self._build_attempted = False
        self._build_lock = threading.Lock()

        self._cache: OrderedDict[tuple[str, str], list[dict[str, Any]]] = OrderedDict()
        self.cache_hits = 0
        self.cache_misses = 0
        self.explain_count = 0

    # -- component resolution -------------------------------------------------

    def _resolve_classifier(self) -> Any:
        if self._classifier is not None:
            return self._classifier
        from app.core.detectors.ml_classifier import get_shared_classifier

        return get_shared_classifier()

    @staticmethod
    def _model_version(classifier: Any) -> str:
        """Identity of the model artifact; changes when the file is rebuilt."""
        model_path = getattr(classifier, "model_path", None)
        if model_path is None:
            return "unknown"
        path = Path(model_path)
        try:
            stat = path.stat()
            raw = f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
        except OSError:
            raw = str(path)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # -- cache ----------------------------------------------------------------

    def _cache_get(self, key: tuple[str, str]) -> Optional[list[dict[str, Any]]]:
        value = self._cache.get(key)
        if value is None:
            self.cache_misses += 1
            return None
        self._cache.move_to_end(key)
        self.cache_hits += 1
        return value

    def _cache_set(self, key: tuple[str, str], value: list[dict[str, Any]]) -> None:
        self._cache[key] = list(value)
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_max_entries:
            self._cache.popitem(last=False)

    def clear_cache(self) -> None:
        """Drop all cached explanations (used by tests and on model reload)."""
        self._cache.clear()

    # -- explainer construction ------------------------------------------------

    @property
    def engine(self) -> Optional[str]:
        """Name of the active engine: ``transformers`` | ``onnx`` | None."""
        return self._engine

    @property
    def available(self) -> bool:
        return self._build_explainer() is not None

    def _build_explainer(self) -> Any:
        """Lazily build the SHAP explainer once (thread-safe)."""
        if self._build_attempted:
            return self._explainer
        with self._build_lock:
            if self._build_attempted:
                return self._explainer
            self._build_attempted = True
            self._explainer, self._engine = self._create_explainer()
            if self._explainer is None:
                logger.warning("explainability_unavailable", engine=None)
            else:
                logger.info("explainability_ready", engine=self._engine)
            return self._explainer

    def _create_explainer(self) -> tuple[Any, Optional[str]]:
        try:
            import shap
        except ImportError as exc:
            logger.warning("explainability_shap_missing", error=str(exc))
            return None, None

        classifier = self._resolve_classifier()
        if classifier is None or not getattr(classifier, "available", False):
            return None, None

        # Preferred: shap.Explainer over a transformers pipeline (Prompt 19).
        pipeline = self._build_transformers_pipeline(classifier)
        if pipeline is not None:
            try:
                return shap.Explainer(pipeline), "transformers"
            except Exception as exc:  # noqa: BLE001 — degrade to ONNX engine
                logger.warning(
                    "explainability_pipeline_explainer_failed", error=str(exc)
                )

        # Production fallback: shap.Explainer over the ONNX model callable.
        try:
            tokenizer = self._load_tokenizer(classifier)
            if tokenizer is None:
                return None, None
            return shap.Explainer(self._build_onnx_callable(classifier), tokenizer), "onnx"
        except Exception as exc:  # noqa: BLE001 — explainability is optional
            logger.warning("explainability_onnx_explainer_failed", error=str(exc))
            return None, None

    def _build_transformers_pipeline(self, classifier: Any) -> Any:
        """Return a HF text-classification pipeline, or None if not usable."""
        try:
            import torch  # noqa: F401
        except ImportError:
            return None
        try:
            from transformers import AutoModelForSequenceClassification, pipeline

            model_path = Path(getattr(classifier, "model_path", ""))
            tokenizer_path = getattr(classifier, "tokenizer_path", None)
            if not tokenizer_path:
                return None
            model = AutoModelForSequenceClassification.from_pretrained(str(model_path.parent))
            return pipeline(
                "text-classification",
                model=model,
                tokenizer=str(tokenizer_path),
                top_k=None,  # return every label so SHAP gets a full vector
            )
        except Exception as exc:  # noqa: BLE001 — torch/HF weights are optional
            logger.info("explainability_pipeline_unavailable", error=str(exc))
            return None

    @staticmethod
    def _build_onnx_callable(classifier: Any) -> Callable[[list[str]], np.ndarray]:
        """Wrap the ONNX classifier into ``list[str] -> [batch, num_classes]``."""

        def _score(texts: list[str]) -> np.ndarray:
            model = getattr(classifier, "_model", None)
            if model is None or not model.is_available():
                raise RuntimeError("classifier model unavailable")
            probs = model.predict(list(texts))
            return np.asarray(probs, dtype=float)

        return _score

    @staticmethod
    def _load_tokenizer(classifier: Any) -> Any:
        tokenizer_path = getattr(classifier, "tokenizer_path", None)
        if not tokenizer_path:
            return None
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(str(tokenizer_path))

    # -- explanation -----------------------------------------------------------

    def _special_tokens(self) -> set[str]:
        try:
            tokenizer = self._explainer.masker.tokenizer
            return set(getattr(tokenizer, "all_special_tokens", None) or [])
        except Exception:  # noqa: BLE001 — never fail over cosmetics
            return set()

    def _class_index(self, top_class: str) -> Optional[int]:
        """Map a class name to the model's output index."""
        id2label = getattr(getattr(self._explainer, "model", None), "id2label", None)
        if isinstance(id2label, dict) and id2label:
            for index, label in id2label.items():
                if str(label).lower() == top_class.lower():
                    return int(index)
        if top_class in CLASS_NAMES:
            return CLASS_NAMES.index(top_class)
        return None

    def _truncate(self, text: str) -> str:
        """Cut the prompt down to ``max_length`` tokens before explaining."""
        if self.max_length <= 0:
            return text
        try:
            tokenizer = self._explainer.masker.tokenizer
            ids = tokenizer.encode(text, add_special_tokens=False)
        except Exception:  # noqa: BLE001 — keep the original text as-is
            return text
        if len(ids) <= self.max_length:
            return text
        try:
            return tokenizer.decode(ids[: self.max_length], skip_special_tokens=True)
        except Exception:  # noqa: BLE001
            return text

    def _explain_sync(
        self, text: str, class_index: int
    ) -> tuple[list[str], np.ndarray]:
        """Run SHAP in a worker thread. Returns (tokens, per-class values)."""
        explanation = self._explainer([self._truncate(text)])

        data = getattr(explanation, "data", None)
        if isinstance(data, tuple):
            data = data[0] if len(data) == 1 else data
        elif isinstance(data, list) and data and not isinstance(data[0], str):
            data = data[0]
        tokens = [str(t) for t in np.asarray(data, dtype=object).reshape(-1)]

        values = np.asarray(getattr(explanation, "values", []), dtype=float)
        if values.ndim == 3:  # (batch, tokens, classes)
            values = values[0][:, class_index]
        elif values.ndim == 2:  # (batch, tokens) for a single-output model
            values = values[0] if values.shape[0] == 1 else values[:, class_index]
        elif values.ndim == 0:
            values = values.reshape(1)
        return tokens, values

    def _select_top_k(self, tokens: list[str], values: np.ndarray) -> list[dict[str, Any]]:
        """Rank tokens by |SHAP| and keep the top_k informative ones."""
        special = self._special_tokens()
        pairs = [
            (token, float(value))
            for token, value in zip(tokens, values)
            if token.strip() and token not in special
        ]
        if not pairs:
            return []
        total = sum(abs(value) for _, value in pairs)
        pairs.sort(key=lambda pair: -abs(pair[1]))
        selected = pairs[: max(self.top_k, 0)]
        return [
            {
                "token": token,
                "shap_value": round(value, 6),
                "contribution": round(abs(value) / total, 4) if total > 0 else 0.0,
            }
            for token, value in selected
        ]

    async def explain(
        self, text: str, top_class: Optional[str] = None
    ) -> Optional[list[dict[str, Any]]]:
        """Explain a classification decision.

        Args:
            text: The classified prompt.
            top_class: Predicted class name; when omitted the shared
                classifier is consulted.

        Returns:
            Top-k ``{token, shap_value, contribution}`` dicts ordered by
            importance, or None when explainability is unavailable/disabled.
        """
        if not self.enabled:
            _outcome("disabled")
            return None
        if not text or not text.strip():
            return None

        classifier = self._resolve_classifier()
        if classifier is None or not getattr(classifier, "available", False):
            _outcome("degraded")
            return None

        key = (self._model_version(classifier), hashlib.sha256(text.encode()).hexdigest())
        cached = self._cache_get(key)
        if cached is not None:
            _outcome("cache_hit")
            logger.debug("explainability_cache_hit", prompt_hash=key[1])
            return cached

        if top_class is None:
            try:
                result = await classifier.classify(text)
            except Exception as exc:  # noqa: BLE001 — degrade
                logger.warning("explainability_classify_failed", error=str(exc))
                _outcome("degraded")
                return None
            if result is None:
                _outcome("degraded")
                return None
            top_class = result.top_class

        explainer = self._build_explainer()
        if explainer is None:
            _outcome("degraded")
            return None

        class_index = self._class_index(top_class)
        if class_index is None:
            logger.warning("explainability_unknown_class", top_class=top_class)
            _outcome("degraded")
            return None

        start = time.perf_counter()
        try:
            loop = asyncio.get_running_loop()
            tokens, values = await asyncio.wait_for(
                loop.run_in_executor(None, self._explain_sync, text, class_index),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "explainability_timeout",
                timeout_seconds=self.timeout_seconds,
                engine=self._engine,
            )
            _outcome("timeout")
            return None
        except Exception as exc:  # noqa: BLE001 — explainability is optional
            logger.warning("explainability_failed", error=str(exc), engine=self._engine)
            _outcome("error")
            return None

        payload = self._select_top_k(tokens, values)
        if not payload:
            _outcome("degraded")
            return None

        self._cache_set(key, payload)
        self.explain_count += 1
        duration = time.perf_counter() - start
        scanner_explainability_duration_seconds.observe(duration)
        _outcome("ok")
        logger.info(
            "explainability_computed",
            engine=self._engine,
            top_class=top_class,
            tokens=len(payload),
            duration_ms=round(duration * 1000, 2),
        )
        return payload

    def get_stats(self) -> dict[str, Any]:
        """Operational statistics for the health endpoint / debugging."""
        return {
            "enabled": self.enabled,
            "engine": self._engine,
            "available": bool(self.enabled) and self._build_explainer() is not None,
            "top_k": self.top_k,
            "timeout_seconds": self.timeout_seconds,
            "max_length": self.max_length,
            "cache_entries": len(self._cache),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "explain_count": self.explain_count,
        }


# Shared singleton (module-level, mirroring ml_classifier conventions)
_shared_explainer: Optional[ClassifierExplainer] = None


def get_shared_explainer() -> ClassifierExplainer:
    """Get (lazily create) the shared explainability singleton."""
    global _shared_explainer
    if _shared_explainer is None:
        _shared_explainer = ClassifierExplainer()
        logger.info("Shared explainer initialized")
    return _shared_explainer


def set_shared_explainer(explainer: Optional[ClassifierExplainer]) -> None:
    """Replace (or reset) the shared explainability singleton."""
    global _shared_explainer
    _shared_explainer = explainer
    logger.info("Shared explainer updated")


async def explain_classification(
    text: str, top_class: Optional[str] = None
) -> Optional[list[dict[str, Any]]]:
    """Convenience wrapper around the shared explainer."""
    return await get_shared_explainer().explain(text, top_class)
