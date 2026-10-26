# План: Промпт 17 (EP-10) — ONNX int8 export classifier

**Источник:** `analytics/ALPHA-PROMPTS.md`, «Промпт 17 — ONNX int8 export» (стр. 589–611).
**Цель:** конвертировать обученный prompt-injection classifier в ONNX FP32 + int8, провалидировать деградацию F1 на test set (< 2% от FP32 baseline), оставить артефакты в `ml-models/classifier/v1/`.

## Существующий контекст (найдено при exploration)

- **Эталон** (Промпт 8, EP-08): `backend/app/ml/export_onnx_int8.py` + `validate_onnx.py` — тот же паттерн (dataclass-отчёт, exit code, `_require_export_deps`, `build_parser`/`main`, structlog), адаптируем под classification.
- **Инференс (Промпт 18) уже готов**: `backend/app/core/detectors/ml_classifier.py` — контракт: входы `input_ids`/`attention_mask`/`token_type_ids` INT64 `[batch, seq]`, **первый выход = logits `[batch, 4]`**, softmax → 4 класса. Путь из `app/config.py:82`: `ml-models/classifier/v1/model_int8.onnx`.
- **Обученная модель отсутствует**: `backend/ml-models/classifier/v1/` содержит только `train/val/test.jsonl` (данные из Промпта 16, сгенерированы; формат `{text, label, source}`, 4 класса 0–3). `model.safetensors`/`config.json`/`tokenizer.json` нет → нужен smoke-train как prerequisite (решение пользователя: smoke-обучение + экспорт).
- **Данные и конфиги используют относительные пути** `ml-models/classifier/v1` (от CWD=backend): `train_classifier.py:157-158`, `app/config.py:82-83`. Экспорт-скрипт должен использовать тот же дефолт (НЕ `REPO_ROOT`-путь, как у embeddings-скрипта).
- **Зависимости уже в pyproject.toml**: base — `onnxruntime`, `transformers`; extra `export` — `torch`, `optimum[onnxruntime]`, `onnx`; extra `train` — `sklearn`, `matplotlib`, `datasets`.
- **Фактический синтаксис optimum-cli 2.1.0** (проверен, отличается от текста промпта):
  - экспорт: `optimum-cli export onnx --model <dir-or-id> --task text-classification --opset 17 <output_dir>` → `model.onnx`;
  - квантизация: `optimum-cli onnxruntime quantize --onnx_model <dir> -o <out_dir> --avx2` → `model_quantized.onnx` (арх. флаг: avx2 для on-prem CPU).
- Тестовый фреймворк: pytest, `uv run pytest ... --no-cov` (иначе триггерится gate `--cov-fail-under=90`); маркер `integration` + `skipif(not artifact.exists())` — паттерн из `tests/ml/test_onnx_embedding.py`.

## Scope (3 файла из промпта)

1. `backend/app/ml/export_classifier_onnx.py` (создать)
2. `backend/app/ml/validate_onnx_classifier.py` (создать)
3. `backend/tests/ml/test_classifier_onnx.py` (создать)

**Порядок выполнения (принципиально):** validate-модуль пишется до export-скрипта (export его импортирует).
**Out of scope:** changes в `train_classifier.py`, интеграция в `main.py` (уже есть), CI, док-файлы (кроме микро-правки docs — см. Шаг 6).

## Шаг 1 — `backend/app/ml/validate_onnx_classifier.py`

Критерий (решение пользователя): **baseline = FP32 ONNX** (`model.onnx`), сравнение на одном test set:

`degradation = f1_fp32 − f1_int8`; `passed = degradation < max_degradation (default 0.02)`.

Содержимое (стиль `validate_onnx.py`):

- Константы: `DEFAULT_MAX_DEGRADATION = 0.02`, `DEFAULT_MAX_LENGTH = 512`, `DEFAULT_CLASSES = ["benign","prompt_injection","jailbreak","suspicious"]`.
- `load_test_set(path) -> list[tuple[str,int]]` — чтение JSONL (`text`, `label`), валидация непустоты и диапазона меток.
- Класс `OnnxF1Evaluator`:
  - `__init__(model_path, tokenizer_path, classes, max_length)` — `ort.InferenceSession(..., providers=["CPUExecutionProvider"])` + `AutoTokenizer.from_pretrained`;
  - `predict(texts) -> np.ndarray` — tokenize (`padding="max_length"`, `truncation=True`, `return_tensors="np"`) → feeds INT64 (включая `token_type_ids`, если есть в графе — паттерн из `validate_onnx.OnnxEmbedder`) → `session.run(None, feeds)[0]` → `argmax(-1)`.
- `@dataclass ValidationReport`: `num_examples`, `baseline_f1`, `int8_f1`, `degradation`, `max_degradation`, `baseline_f1_per_class`, `int8_f1_per_class`, свойство `passed` (`num_examples > 0 and degradation < max_degradation`), `to_dict(include_per_class=True)`.
- `evaluate_f1(y_true, y_pred) -> tuple[float, dict[str,float]]` — обёртка над `app.ml.metrics.compute_metrics` (reuse, консистентность с обучением; требует extra `train` — ок, валидация идёт после обучения).
- `validate(fp32_model_path, int8_model_path, tokenizer_path, test_set_path, max_degradation=0.02, max_length=512) -> ValidationReport` — два evaluator'а, один проход по test set, две F1.
- `build_parser()`: `--fp32`, `--int8`, `--tokenizer`, `--test-set`, `--max-degradation`, `--max-length`, `--report <json path>` (write полного to_dict), `--baseline-f1` (optional override — если FP32 файл недоступен, сравнить int8 с заданным числом).
- `main() -> int`: печать summary JSON, exit 0 при pass / 1 при fail (сообщение в stderr, как в `validate_onnx.py:311-317`).

## Шаг 2 — `backend/app/ml/export_classifier_onnx.py`

Пайплайн (шаги промпта, синтаксис optimum-cli 2.1.0):

1. `_require_export_deps()` — torch/optimum/onnx (копия из `export_onnx_int8.py:49`).
2. Проверка входа: в `<model_dir>` должны быть `config.json` + `model.safetensors` (или `pytorch_model.bin`) + токенайзер — иначе `SystemExit` с подсказкой запустить `python -m app.ml.train_classifier`.
3. `export_fp32(model_dir, opset=17) -> Path`:
   - `_run_optimum_cli([...])` — subprocess `[optimum-cli_path_or_sys.executable -m optimum.cli, "export", "onnx", "--model", model_dir, "--task", "text-classification", "--opset", str(opset), tmp_dir]`; `shutil.which("optimum-cli")` с fallback на `[sys.executable, "-m", "optimum.cli"]`; check=True, stdout/stderr в structlog;
   - забрать `model.onnx` из tmp → переместить в `model_dir/model.onnx` (артефакт FP32 baseline);
   - `normalize_classifier_contract(model.onnx)`: входы обязаны содержать `input_ids`+`attention_mask` (INT64, как `export_onnx_int8.py:83-87`), первый выход переименовать в `logits`, лишние выходы удалить; **не переписывать файл, если контракт уже ок** (паттерн `export_onnx_int8.py:103-110`);
   - smoke: `ort.InferenceSession(model.onnx, providers=["CPUExecutionProvider"])`.
4. `quantize_int8(fp32_path) -> Path`:
   - `_run_optimum_cli(["onnxruntime", "quantize", "--onnx_model", tmp_dir_with_model.onnx, "-o", quant_out, "--avx2"])`; в quant_out найти единственный `*.onnx` (обычно `model_quantized.onnx`) → скопировать в `model_dir/model_int8.onnx`;
   - `normalize_classifier_contract` + smoke-load int8.
5. `run_validation(fp32_path, int8_path, model_dir) -> bool` — вызов `app.ml.validate_onnx_classifier.validate` (test set = `model_dir/test.jsonl`), лог отчёта, return `report.passed`.
6. `export_classifier(model_dir="ml-models/classifier/v1", opset=17, max_degradation=0.02, skip_validate=False) -> Path` — оркестрация; при failed validation → `SystemExit("int8 validation failed ...")`, артефакты остаются для инспекции (семантика из `export_onnx_int8.py:322-327`).
7. `build_parser()`: `--model-dir` (default `ml-models/classifier/v1`), `--opset`, `--max-degradation` (default 0.02), `--skip-validate`; `main(argv) -> int`.

## Шаг 3 — `backend/tests/ml/test_classifier_onnx.py`

Паттерн `tests/ml/test_onnx_embedding.py`: `BACKEND_ROOT = Path(__file__).resolve().parents[2]`; `INT8_MODEL = BACKEND_ROOT/"ml-models"/"classifier"/"v1"/"model_int8.onnx"` и т.д.

- `TestLoadTestSet` — чтение tmp JSONL, ошибка на пустой/битый файл, вне-диапазонная метка.
- `TestValidationReport` — pass при degradation 0.019, fail при 0.02+; пустой набор never passes; `to_dict` shape.
- `TestOnnxF1Evaluator` — mock `ort.InferenceSession` + `AutoTokenizer` (паттерн `_make_embedder`): logits `[1,4]` → argmax; feeds INT64; `token_type_ids` прокидывается при наличии в графе.
- `TestNormalizeClassifierContract` — tiny ONNX-модели через `onnx.helper` (`pytest.importorskip("onnx")`): переименование первого выхода в `logits`, удаление extra outputs, missing `attention_mask` → RuntimeError, уже-валидный контракт → файл не переписывается.
- `TestRunOptimumCli` — mock `subprocess.run`: сборка аргументов (`export onnx` / `onnxruntime quantize`), non-zero exit → CalledProcessError→SystemExit с message.
- `TestExportFp32` / `TestQuantizeInt8` — mock `_run_optimum_cli` + фабрика tmp-файлов: перемещение `model.onnx`, поиск `*.onnx` в quant_out → `model_int8.onnx`, вызов normalize/smoke.
- `TestExportClassifier` — happy path (mock всех шагей), validation-fail → SystemExit, `skip_validate`, отсутствие `model.safetensors` → SystemExit с подсказкой train.
- `TestExportCli` / `TestValidateCli` — defaults, exit codes 0/1.
- `@pytest.mark.integration` + `skipif(not INT8_MODEL.exists() or not TEST_SET.exists())` — реальный прогон: int8 vs FP32 F1 degradation < 0.02 на `test.jsonl` (или только int8 F1-скор, если FP32 удалён).

## Шаг 4 — Smoke-обучение (prerequisite для реального экспорта)

```bash
cd backend
uv sync --extra train --extra export    # если окружение не свежее
uv run python -m app.ml.train_classifier --smoke --min-f1 0
```

- `--smoke`: 200 примеров, 1 эпоха, max_length 128; `--min-f1 0` — gate качества не блокирует (артефакты и так сохраняются до проверки F1, `train_classifier.py:298`); default base model `bert-base-uncased` (~440 MB, первый запуск скачает в HF cache).
- Результат: `backend/ml-models/classifier/v1/{model.safetensors, config.json, tokenizer.json, metrics.json, ...}` + уже существующие `test.jsonl`.

## Шаг 5 — Реальный экспорт + проверка

```bash
cd backend
uv run python -m app.ml.export_classifier_onnx
# → optimum-cli export (FP32 model.onnx) → optimum-cli onnxruntime quantize --avx2 (model_int8.onnx)
# → валидация: F1 int8 vs FP32 на test.jsonl, degradation < 0.02, exit 0
uv run pytest tests/ml/test_classifier_onnx.py -v --no-cov          # unit
uv run pytest tests/ml/test_classifier_onnx.py -m integration -v --no-cov  # реальная валидация
```

Дополнительно (sanity): `uv run python -c` с `PromptClassifier("ml-models/classifier/v1/model_int8.onnx", ...)` — проверка, что детектор (Промпт 18) поднимается на новых артефактах и выдаёт 4-классовый top_class.

Ожидаемые размеры: FP32 ~440 MB, int8 ~110 MB (bert-base-uncased; если позже переучат на deberta-v3-small — ~560/140 MB). Артефакты в `.gitignore` (`ml-models/**/*.onnx` — уже настроен).

## Шаг 6 — Микро-док (опционально, минимально)

`docs/ml-training.md` уже ссылается на `export_classifier_onnx.py` — добавить в раздел Integration команды экспорта/валидации (3-5 строк). Файл из промпта не указан, поэтому по желанию; можно пропустить.

## Риски / особенности

- Синтаксис optimum-cli отличается от промпта (старый `quantize --onnxruntime --int8`) — реализуется фактический 2.1.0 CLI (проверено).
- Имя выходного файла квантизации (`model_quantized.onnx`) не гарантировано — поиск по glob `*.onnx` в output dir.
- Smoke-модель слабая по качеству; критерий валидации **относительный** (int8 vs FP32), поэтому проходит осмысленно. Абсолютный F1-gate остаётся за Промптом 16/20.
- `validate_onnx_classifier` тянет `matplotlib` (via `app.ml.metrics` import) — доступен в extra `train`; в CI/окружении без train extra import упадёт → в докстринге указать `uv sync --extra train --extra export`.
- Windows: subprocess без shell, list-аргументы; `--avx2` — корректный флаг для on-prem CPU.

## Верификация (definition of done)

1. 3 файла созданы, unit-тесты зелёные (`pytest tests/ml/test_classifier_onnx.py --no-cov`).
2. `ml-models/classifier/v1/model.onnx` + `model_int8.onnx` существуют, оба грузятся в `ort.InferenceSession(CPU)`.
3. `python -m app.ml.export_classifier_onnx` завершается с exit 0 (валидация пройдена), отчёт печатается.
4. Integration-тест реагирует на presence артефактов (не скипается) и проходит.
5. `PromptClassifier` из `ml_classifier.py` успешно классифицирует тестовый промпт через `model_int8.onnx`.
