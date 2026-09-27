# ADR-0010: ML Classifier — DeBERTa-v3-small fine-tuned + ONNX

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-09-27 |
| **Deciders** | Architecture Team, ML Engineering, SecEng |
| **Related** | ADR-0002, ADR-0005, ADR-0008, ADR-0013 |

## Context

Векторный поиск ловит **похожие** на известные атаки промпты. Но novel attacks (новые джейлбрейки, не похожие на существующий corpus) — пропустит. Нужен ML-классификатор, обученный на широком corpus prompt-injection/jailbreak примеров, способный **обобщать** на новые атаки.

Требования:
- p99 latency: < 15 ms (на GPU), < 40 ms (на CPU).
- Классы: `prompt_injection`, `jailbreak`, `benign`, `suspicious`.
- Multilingual (RU + EN).
- Fine-tunable на наших labeled данных (continuous learning).
- Cost-effective (CPU-friendly для on-prem без GPU).

### Forces

- **Точность**: F1 > 0.90 на prompt-injection detection.
- **Latency**: не должна пробивать p99 бюджет slow path.
- **Multilingual**: русские джейлбрейки отличаются от английских (ДАН-варианты в RU).
- **Обновляемость**: continuous retraining на основе feedback loop (ADR-0013).
- **Explainability**: SecOps должен понимать, почему промпт помечен (attention weights / SHAP).
- **CPU inference**: для on-prem air-gapped без GPU.

## Decision

**Принять DeBERTa-v3-small fine-tuned + ONNX Runtime.**

### Базовая модель

**`microsoft/deberta-v3-small`**:
- 140M параметров,
- ~500 MB (FP32), ~125 MB (int8),
- Disentangled attention mechanism (лучше ловит долгие зависимости),
- Лучше RoBERTa на NLI задачах при том же размере.

### Fine-tuning

На corpus:
- **Prompt injection datasets**: AdvBench, JailbreakBench, prompt-injection open-source corpora (~50K examples),
- **Custom labeled data**: из feedback loop (ADR-0013) — SecOps-размеченные FP/FN,
- **Synthetic**: GCG-атаки, adversarial perturbations (auto-generated).

Классы:
- `benign` — нормальные промпты,
- `prompt_injection` — инъекции в промпт,
- `jailbreak` — попытки обхода safety instructions,
- `suspicious` — пограничные случаи (route_to_human).

Multilingual fine-tuning: совмещение RU+EN corpus + XLM-RoBERTa fallback для редких языков.

### Inference runtime

| Опция | Latency (GPU, batch=1) | Latency (CPU, batch=1) | Размер |
|---|---|---|---|
| ONNX Runtime + int8 | 4 ms | 25 ms | 125 MB |
| ONNX Runtime + FP16 | 6 ms | 40 ms | 250 MB |
| PyTorch native | 10 ms | 60 ms | 500 MB |

**Выбор:** ONNX Runtime + int8 для prod (CPU-friendly + быстрый).

### Pipeline

```python
class PromptClassifier:
    def __init__(self):
        self.session = ort.InferenceSession(
            "models/deberta_v3_small_int8.onnx",
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
        )
        self.tokenizer = AutoTokenizer.from_pretrained("microsoft/deberta-v3-small")

    def classify(self, text: str) -> dict:
        inputs = self.tokenizer(text, return_tensors="np", max_length=512, truncation=True)
        outputs = self.session.run(None, dict(inputs))
        probs = softmax(outputs[0][0])
        return {
            "benign": float(probs[0]),
            "prompt_injection": float(probs[1]),
            "jailbreak": float(probs[2]),
            "suspicious": float(probs[3]),
            "top_class": CLASSES[probs.argmax()],
            "confidence": float(probs.max()),
        }
```

### Decision thresholds (initial, требуют тюнинга)

| Класс | Threshold (confidence) | Действие PDP |
|---|---|---|
| `prompt_injection` | > 0.85 | BLOCK |
| `prompt_injection` | 0.60–0.85 | ROUTE_TO_HUMAN |
| `prompt_injection` | 0.40–0.60 | LOG_ONLY + flag |
| `jailbreak` | > 0.80 | BLOCK |
| `suspicious` | > 0.70 | ROUTE_TO_HUMAN |
| `benign` | — | (по умолчанию) |

### Retraining

- Schedule: weekly batch retraining on accumulated labeled data.
- Validation: hold-out test set (stratified by class), baseline F1 сравнивается с новой моделью.
- Auto-rollback: если F1 новой модели хуже baseline более 5% — откат, алерт в ML team.
- Canary deploy: новая модель на 5% трафика в течение 24 часов, сравнение FP/FN метрик.

### Explainability

- **SHAP values** для top-K токенов, влияющих на классификацию.
- В audit log: top-5 tokens с SHAP — SecOps понимает, **почему** промпт помечен.
- Пример: `"ignore previous instructions"` → SHAP выделяет `"ignore previous"`.

## Consequences

### Positive

- ✅ Ловит novel attacks, не похожие на существующий corpus (главное преимущество перед vector search).
- ✅ CPU-friendly (int8 ONNX) — работает on-prem без GPU.
- ✅ Continuous improvement через retrain loop.
- ✅ Explainability через SHAP — compliance-friendly.
- ✅ Multilingual: fine-tuning на RU+EN corpus.

### Negative

- ❌ False positives на edge-cases (legitimate prompts с инструкциями). Mitigation: feedback loop + retrain.
- ❌ Fine-tuning требует labeled corpus — на старте мало RU-данных. Mitigation: bootstrap из EN + перевод + активное обучение.
- ❌ 25 ms на CPU — может пробить p99 при большой нагрузке. Mitigation: batching (3× faster).
- ❌ Concept drift: новые атаки не похожи на старые. Mitigation: weekly retrain + threat-intel sync (ADR-0012).

### Neutral

- ➖ Модель обновляется weekly — нужен model registry (MLflow / DVC).
- ➖ 4 класса — баланс между гранулярностью и точностью. Возможно расширить до 6+ (Phase 3).

## Alternatives Considered

### Alternative A: LLM-as-judge (GPT-4 / Claude как классификатор)

| Аспект | Оценка |
|---|---|
| Pros | Максимальная точность, не нужно fine-tuning, handle novel attacks well |
| Cons | Latency 500–2000 ms (network + LLM inference), cost $0.01–0.05 per call, privacy (data уходит в OpenAI/Anthropic), non-determinism |
| Why rejected | Latency недопустима для inline. Privacy violation. Cost × 1000 RPS = $10K–50K/day |

**Вердикт:** Возможно в async post-factum для high-risk flagged запросов (Phase 3).

### Alternative B: RoBERTa-large fine-tuned

| Аспект | Оценка |
|---|---|
| Pros | Зрелая модель, много pre-trained checkpoints, хорошо benchmark'ается |
| Cons | 355M params (тяжелее DeBERTa-v3-small в 2.5×), не disentangled attention, медленнее |
| Why rejected | DeBERTa-v3 даёт сопоставимое качество при меньшем размере и latency |

**Вердикт:** Отклонено.

### Alternative C: BERT-base multilingual

| Аспект | Оценка |
|---|---|
| Pros | Multilingual from scratch, 110M params |
| Cons | Не специализирован на NLI-задачах, дообучается на нашем corpus |
| Why rejected | DeBERTa-v3-small лучше на prompt-injection classification (по нашим benchmarks) |

**Вердикт:** Отклонено.

### Alternative D: BGE-Reranker или cross-encoder

| Аспект | Оценка |
|---|---|
| Pros | Отличная точность, well-suited для classification через similarity |
| Cons | Pairwise: нужен corpus «эталонов» для сравнения — медленнее (N comparisons) |
| Why rejected | Single-input classification быстрее |

**Вердикт:** Возможно в Phase 3 для ranking/refinement stage.

### Alternative E: Custom transformer model с нуля

| Аспект | Оценка |
|---|---|
| Pros | Полный контроль архитектуры |
| Cons | NRE cost: 6+ месяцев ML-работы, нужен большой корпус, риск недотянуть до pre-trained baseline |
| Why rejected | Pre-trained DeBERTa-v3 + fine-tune = 95% качества custom-модели при 5% effort |

**Вердикт:** Отклонено. Возможно Phase 5+.

## Related Decisions

- **ADR-0002** (Fast/Slow Path) — classifier только в slow path.
- **ADR-0005** (Qdrant) — Qdrant даёт похожие атаки, classifier — обобщение.
- **ADR-0008** (Circuit Breaker) — fallback на rule-only при недоступности classifier.
- **ADR-0013** (Feedback Loop) — labels из feedback идут в retrain pipeline.

## References

- [DeBERTa-v3 paper](https://arxiv.org/abs/2111.09543)
- [AdvBench dataset](https://github.com/llm-attacks/llm-attacks)
- [JailbreakBench](https://jailbreakbench.github.io/)
- [ONNX Runtime](https://onnxruntime.ai/)
- [SHAP for text classification](https://shap.readthedocs.io/en/latest/example_notebooks/text_examples/text_classification.html)
- ARCHITECT.md, раздел 7.6 — ML Classifier component
