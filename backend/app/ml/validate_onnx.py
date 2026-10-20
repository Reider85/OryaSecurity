"""Quality check: cosine similarity of int8 ONNX embeddings vs FP32 baseline.

Compares embeddings produced by two ONNX graphs (FP32 baseline vs int8
quantized) on a fixed set of 100 test prompts. The int8 model is accepted
when the mean cosine similarity exceeds a threshold (default 0.95).

Usage:
    python -m app.ml.validate_onnx \
        --fp32 ml-models/embeddings/1/model.onnx \
        --int8 ml-models/embeddings/1/model_int8.onnx \
        --tokenizer ml-models/embeddings/1

Exit code 0 when validation passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

DEFAULT_THRESHOLD = 0.95
DEFAULT_MAX_LENGTH = 512
DEFAULT_PREFIX = "query: "

# 100 deterministic test prompts (en + ru, benign, varied lengths/topics).
_BASE_PROMPTS: list[str] = [
    "What is the capital of France?",
    "Summarize the plot of Romeo and Juliet in two sentences.",
    "Write a Python function that reverses a string.",
    "Explain the difference between TCP and UDP.",
    "How do vaccines work?",
    "Translate 'good morning' to Spanish.",
    "List three renewable energy sources.",
    "Describe the water cycle.",
    "Give me tips for improving sleep quality.",
    "Explain quantum entanglement to a high-school student.",
    "What is a binary search tree?",
    "How does photosynthesis work?",
    "Write a haiku about autumn.",
    "What causes tides on Earth?",
    "Explain the theory of relativity briefly.",
    "How do I center a div in CSS?",
    "What is the difference between a list and a tuple in Python?",
    "Describe the steps of the scientific method.",
    "What is compound interest?",
    "How does DNS resolution work?",
    "Name the planets of the solar system in order.",
    "What is the role of mitochondria in a cell?",
    "Explain what machine learning is in one paragraph.",
    "How does encryption keep data safe?",
    "What are the symptoms of the common cold?",
    "Write a polite email declining a meeting invitation.",
    "Explain the difference between RAM and ROM.",
    "What is the boiling point of water at sea level?",
    "How do airplanes generate lift?",
    "What is a REST API?",
    "Summarize the causes of World War I.",
    "Explain how GPS determines location.",
    "What is the golden ratio?",
    "How does a hashmap achieve constant-time lookup?",
    "What are the health benefits of regular exercise?",
    "Explain the difference between weather and climate.",
    "How does noise cancellation work in headphones?",
    "What is the significance of the number pi?",
    "Describe how a combustion engine works.",
    "What is a neural network?",
    "How does brick-and-mortar retail differ from e-commerce?",
    "Write a short bedtime story about a friendly dragon.",
    "What is the difference between an array and a linked list?",
    "Explain how herd immunity works.",
    "What are the main ingredients of bread?",
    "How does the human ear detect sound?",
    "What is an operating system kernel?",
    "Explain the concept of supply and demand.",
    "How do volcanic eruptions form?",
    "Что такое искусственный интеллект?",
    "Какие бывают типы данных в Python?",
    "Объясни разницу между SQL и NoSQL.",
    "Напиши краткое изложение сюжета «Войны и мира».",
    "Как работает синхронный двигатель?",
    "Переведи фразу «Добрый вечер» на английский.",
    "Что такое квантовый компьютер?",
    "Какие столицы у стран БРИКС?",
    "Опиши процесс фотосинтеза кратко.",
    "В чём разница между протоколами HTTP и HTTPS?",
    "Как устроен механизм защиты от вирусов в организме?",
    "Напиши поздравление с днём рождения коллеге.",
    "Что такое контейнерная виртуализация?",
    "Какие основные законы Ньютона?",
    "Объясни, что такое энтропия.",
    "Как отличить настоящие грибы от ложных?",
    "Что такое блокчейн простыми словами?",
    "Как работает алгоритм сортировки пузырьком?",
    "Какие языки программирования подходят для начинающих?",
    "В чём разница между интеллектуальной и собственностью?",
    "Опиши устройство атома по планетарной модели.",
    "Как подготовиться к собеседованию на позицию разработчика?",
    "Что такое API-ключи и как их безопасно хранить?",
    "Какие продукты богаты витамином C?",
    "Как funciona o ciclo da água? Что означает эта фраза по-русски?",
    "Найди три отличия между кометой и астероидом.",
    "Каково назначение оператора `git rebase`?",
    "Что такое принцип единой ответственности в проектировании?",
    "Объясни разницу между компилятором и интерпретатором.",
    "Какие меры помогают экономить электричество дома?",
    "Что такое маркетинговая воронка?",
    "Опиши порядок действий при эвакуации из здания.",
    "Как работает система антиблокировки тормозов?",
    "Что такое замыкание (closure) в JavaScript?",
    "Какие бывают архитектурные паттерны веб-приложений?",
    "Напиши рецепт борща кратко.",
    "Как определить мошенничество в онлайн-платежах?",
    "Что такое zero-day уязвимость?",
    "Как читать семантику слова по словарю контекста?",
    "Объясни понятие карбонового следа.",
    "Какова роль Организации Объединённых Наций?",
    "Чем отличается резервное копирование от репликации?",
    "Как настроить двухфакторную аутентификацию?",
    "Что такое гидравлический пресс и на чём основан его принцип?",
    "Как помочь ребёнку полюбить чтение?",
    "Опиши разницу между архивацией и сжатием без потерь.",
    "Что такое SRE и чем он отличается от классического сисадмина?",
    "Как киты дышат под водой?",
    "Напиши вводный абзац статьи о пользе бега.",
    "Что такое шаблон проектирования «Наблюдатель»?",
    "Какова скорость света в вакууме и почему она важна?",
]


def default_prompts() -> list[str]:
    """Return the built-in 100-prompt validation set."""
    prompts = [p.strip() for p in _BASE_PROMPTS if p.strip()]
    if len(prompts) != 100:
        raise RuntimeError(f"expected exactly 100 validation prompts, got {len(prompts)}")
    return prompts


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D vectors."""
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def mean_pool_l2(hidden: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Masked mean pooling + L2 normalization (e5 convention).

    hidden: [batch, seq, dim]; attention_mask: [batch, seq].
    Returns [batch, dim] float32 L2-normalized vectors.
    """
    mask_expanded = np.expand_dims(attention_mask, -1).astype(hidden.dtype)
    summed = np.sum(hidden * mask_expanded, axis=1)
    counts = np.clip(np.sum(mask_expanded, axis=1), a_min=1e-9, a_max=None)
    pooled = summed / counts
    norm = np.linalg.norm(pooled, axis=1, keepdims=True)
    pooled = pooled / np.clip(norm, a_min=1e-9, a_max=None)
    return pooled.astype(np.float32)


@dataclass
class ValidationReport:
    num_prompts: int
    threshold: float
    similarities: list[float] = field(default_factory=list)

    @property
    def mean_cosine(self) -> float:
        return float(np.mean(self.similarities)) if self.similarities else 0.0

    @property
    def min_cosine(self) -> float:
        return float(np.min(self.similarities)) if self.similarities else 0.0

    @property
    def p05_cosine(self) -> float:
        if not self.similarities:
            return 0.0
        return float(np.percentile(self.similarities, 5))

    @property
    def passed(self) -> bool:
        return self.num_prompts > 0 and self.mean_cosine > self.threshold

    def to_dict(self, include_per_prompt: bool = False) -> dict:
        data: dict = {
            "num_prompts": self.num_prompts,
            "threshold": self.threshold,
            "mean_cosine": self.mean_cosine,
            "min_cosine": self.min_cosine,
            "p05_cosine": self.p05_cosine,
            "passed": self.passed,
        }
        if include_per_prompt:
            data["similarities"] = self.similarities
        return data


def evaluate_similarities(similarities: Sequence[float], threshold: float = DEFAULT_THRESHOLD) -> ValidationReport:
    """Build a ValidationReport from per-prompt cosine similarities."""
    report = ValidationReport(num_prompts=len(similarities), threshold=threshold)
    report.similarities = [float(s) for s in similarities]
    return report


class OnnxEmbedder:
    """Minimal ONNX Runtime embedder (tokenize → inference → pool+L2)."""

    def __init__(
        self,
        model_path: str | Path,
        tokenizer_path: str | Path,
        max_length: int = DEFAULT_MAX_LENGTH,
        prefix: str = DEFAULT_PREFIX,
    ):
        self.model_path = Path(model_path)
        self.tokenizer_path = Path(tokenizer_path)
        self.max_length = max_length
        self.prefix = prefix
        self.session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.tokenizer_path))

    def embed(self, text: str) -> np.ndarray:
        inputs = self.tokenizer(
            f"{self.prefix}{text}",
            return_tensors="np",
            max_length=self.max_length,
            truncation=True,
            padding=True,
        )
        feeds = {
            "input_ids": inputs["input_ids"].astype(np.int64),
            "attention_mask": inputs["attention_mask"].astype(np.int64),
        }
        available = {i.name for i in self.session.get_inputs()}
        if "token_type_ids" in available and "token_type_ids" in inputs:
            feeds["token_type_ids"] = inputs["token_type_ids"].astype(np.int64)

        outputs = self.session.run(None, feeds)
        hidden = outputs[0]
        return mean_pool_l2(hidden, feeds["attention_mask"])[0]


def validate(
    fp32_model_path: str | Path,
    int8_model_path: str | Path,
    tokenizer_path: str | Path,
    prompts: Optional[Sequence[str]] = None,
    threshold: float = DEFAULT_THRESHOLD,
    max_length: int = DEFAULT_MAX_LENGTH,
) -> ValidationReport:
    """Compare int8 embeddings against the FP32 baseline on test prompts."""
    prompt_list = list(prompts) if prompts is not None else default_prompts()
    if not prompt_list:
        raise ValueError("validation prompt set is empty")

    fp32 = OnnxEmbedder(fp32_model_path, tokenizer_path, max_length=max_length)
    int8 = OnnxEmbedder(int8_model_path, tokenizer_path, max_length=max_length)

    similarities: list[float] = []
    for prompt in prompt_list:
        ref = fp32.embed(prompt)
        got = int8.embed(prompt)
        similarities.append(cosine_similarity(ref, got))

    return evaluate_similarities(similarities, threshold=threshold)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate int8 ONNX embeddings against FP32 baseline")
    parser.add_argument("--fp32", required=True, help="Path to FP32 ONNX model")
    parser.add_argument("--int8", required=True, help="Path to int8 ONNX model")
    parser.add_argument("--tokenizer", required=True, help="Path to tokenizer directory")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="Min mean cosine similarity")
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH, help="Tokenizer max length")
    parser.add_argument("--report", help="Optional path to write a JSON report")
    parser.add_argument("--per-prompt", action="store_true", help="Include per-prompt similarities in the report")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    report = validate(
        args.fp32,
        args.int8,
        args.tokenizer,
        threshold=args.threshold,
        max_length=args.max_length,
    )

    summary = report.to_dict(include_per_prompt=False)
    print(json.dumps(summary, indent=2))
    if args.report:
        Path(args.report).write_text(
            json.dumps(report.to_dict(include_per_prompt=args.per_prompt), indent=2),
            encoding="utf-8",
        )
        print(f"report written: {args.report}", file=sys.stderr)

    if not report.passed:
        print(
            f"VALIDATION FAILED: mean cosine {report.mean_cosine:.4f} <= threshold {report.threshold}",
            file=sys.stderr,
        )
        return 1
    print(f"VALIDATION PASSED: mean cosine {report.mean_cosine:.4f} > {report.threshold}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
