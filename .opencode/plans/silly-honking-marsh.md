# План: Промпт 10 (EP-08) — EmbeddingBatcher

**Источник:** `analytics/ALPHA-PROMPTS.md`, «Промпт 10 — Batching» (стр. 374–402).
**Цель:** micro-batching embedding-запросов перед Triton: очередь → flush по 32 запросам или 5 мс → `triton_client.embed_batch` → раздача результатов через futures.

## Существующий контекст (найдено при exploration)

- `backend/app/ml/triton_client.py` — **уже есть**, метод `embed_batch(texts) -> list[Optional[np.ndarray]]` (gRPC Triton, padding per-batch, pooling per-sequence). ⚠️ **Баг в строке 175:** `list([self._tokenize_sync, text] for text in texts)` создаёт пары `(function, text)` вместо результатов токенизации → `embed_batch` всегда падает в except и возвращает `[None]*n`. Без починки батчер бесполезен.
- `backend/app/core/cache_embedding.py` (Промпт 9) — готов, батчер интегрируется поверх него снаружи (cache → batcher → triton).
- Точка вызова embedding сегодня: `backend/app/core/streaming/post_factum.py::_check_vector_search` → `embedding_client.embed(text)` поштучно. Каталога `core/pipeline/` нет.
- Стиль проекта: `structlog` (событие-строка + kwargs), `prometheus_client` метрики module-level или в `app/core/metrics.py`, degrade-паттерн (warning + метрика + `None`), синглтоны `get/set_shared_*`.
- Тесты: `pytest` + `pytest-asyncio` (`asyncio_mode="auto"`), классы `TestXxx`, `unittest.mock` (patch `InferenceServerClient`), numpy `assert_allclose`.
- Зависимости (`numpy`, `tritonclient[grpc]`, `pytest-asyncio`) уже в `backend/pyproject.toml`. Сервис Triton есть в `docker-compose.alpha.yml`.

## Scope

В рамках промпта — ровно 2 файла + обязательная починка бага:
1. `backend/app/ml/batcher.py` (создать)
2. `backend/tests/ml/test_batcher.py` (создать)
3. `backend/app/ml/triton_client.py` (фикс строки 175 — prerequisite)

**Out of scope** (не упомянуто в промпте, может быть follow-up): интеграция батчера в `main.py::init_post_factum_stack` / `PostFactumAnalyzer`, настройки в `config.py`, OTel.

## Шаг 1 — Починка `triton_client.embed_batch`

`backend/app/ml/triton_client.py:175`:

```python
# было (баг):
tokenized_list = await loop.run_in_executor(None, lambda: list([self._tokenize_sync, text] for text in texts))
# стало:
tokenized_list = await loop.run_in_executor(None, lambda: [self._tokenize_sync(text) for text in texts])
```

Существующий тест `tests/ml/test_triton_embedding.py` покрывает embed, но не embed_batch — добавить smoke-тест embed_batch (mock infer → проверка, что возвращается len(texts) векторов) в этот же файл.

## Шаг 2 — `backend/app/ml/batcher.py`

Класс `EmbeddingBatcher` (полный файл, без псевдокода):

```python
class EmbeddingBatcher:
    def __init__(self, triton_client, max_batch: int = 32, max_wait_ms: float = 5.0): ...
    async def start(self) -> None          # idempotent: создаёт self._task = create_task(self._batch_loop())
    async def stop(self) -> None           # graceful: sentinel → final drain flush → await task
    async def embed(self, text: str) -> Optional[np.ndarray]
    async def _collect_batch(self) -> list[tuple[str, asyncio.Future]] | None   # None = sentinel/stop
    async def _batch_loop(self) -> None
```

Логика:

- **`embed(text)`**:
  - Деградация без очереди: `triton_client is None`, `not text/blank`, `not triton_client.available` → сразу `return None` (соответствует семантике `TritonEmbeddingClient.embed`).
  - Lazy auto-start фонового цикла (guarded, чтобы не создать два таска).
  - `future = asyncio.get_running_loop().create_future()` (не deprecated `get_event_loop`), `await self._queue.put((text, future))`, `return await future`.
- **`_collect_batch()`** (flush-политика из промпта: 5 мс **или** 32 шт.):
  - Блокируемся на первом элементе (`await self._queue.get()`); если это sentinel `_STOP` → возврат `None` (сигнал выхода из цикла).
  - Далее докидываем до `max_batch` с дедлайном `time.monotonic() + max_wait_ms/1000` через `asyncio.wait_for(self._queue.get(), timeout)`; по `TimeoutError` — break. Sentinel в середине батча — вернуть собранный батч + флаг stop (финальный drain).
- **`_batch_loop()`**:
  - `while True`: `batch = await self._collect_batch()`; если `None` → break.
  - Если в batch попал sentinel → после отправки этого батча — drain остатка очереди (последний flush без лимитов, чанками по max_batch) и выход.
  - `results = await self.triton_client.embed_batch([t for t, _ in batch])`; раздать: `future.set_result(result)` (в т.ч. `None` при degrade — тритон-клиент сам гасит ошибки в `[None]*n`).
  - Защита тела цикла: `try/except Exception` → `logger.warning("embedding_batch_failed", ...)` + метрика ошибок + `future.set_result(None)` на весь батч, чтобы ни один future не завис; loop продолжает жить.
  - Тихий выход по `asyncio.CancelledError` (отмена таска) — пробрасываем.
- **`stop()`**: кладёт sentinel, ждёт `self._task`; все futures, не получившие результат (если вдруг остались), получают `asyncio.CancelledError("embedding_batcher_stopped")` — чтобы await'еры не висели.
- **Метрики** (module-level, как в `cache_embedding.py`):
  - `scanner_embedding_batcher_requests_total` (Counter)
  - `scanner_embedding_batcher_batches_total` (Counter)
  - `scanner_embedding_batcher_batch_size` (Histogram, buckets 1..32)
  - `scanner_embedding_batcher_inference_seconds` (Histogram — latency батча)
  - `scanner_embedding_batcher_errors_total` (Counter)
- **Логи** (structlog): `embedding_batcher_started/stopped`, `embedding_batch_flushed` (debug, batch_size, latency_ms), `embedding_batch_failed`.
- Module-level синглтон `get/set_shared_embedding_batcher` — в том же стиле, что `triton_client.py` (на будущую интеграцию).

Типажи: `from __future__ import annotations`, `Optional[np.ndarray]` (реальный контракт тритон-клиента — degrade to `None`), полный docstring класса.

## Шаг 3 — `backend/tests/ml/test_batcher.py`

В стиле `tests/ml/test_triton_embedding.py`: pytest, классы `TestXxx`, без реального Triton — `FakeTritonClient` (stub с `available` + async `embed_batch`, записывает вызовы, возвращает детерминированные векторы) + вариант с raising-стабом.

Тест-кейсы:

1. `test_single_request_flushes_after_wait` — один `embed()` → future резолвится ~через `max_wait_ms` (batch size 1).
2. `test_concurrent_requests_coalesce` — 5 параллельных `embed()` → ровно один вызов `embed_batch` с 5 текстами, каждый получает свой вектор.
3. `test_max_batch_triggers_flush` — 40 параллельных при `max_batch=32` → два батча (32 + 8).
4. `test_result_ordering` — каждый future получает вектор, соответствующий **его** тексту (не перемешан).
5. `test_none_result_propagates` — `embed_batch` вернул `[None, vec]` → соответствующие futures → `None` / вектор.
6. `test_embed_batch_exception_degrades` — `embed_batch` кидает исключение → все futures батча → `None` (не зависают), loop живёт дальше (следующий батч работает), инкремент `errors_total`.
7. `test_short_circuits_when_unavailable` — `available=False` → `embed()` возвращает `None` мгновенно, `embed_batch` не вызывался, очередь пуста.
8. `test_blank_text_short_circuits` — `""` / `"   "` → `None`.
9. `test_stop_flushes_pending` — до `stop()` в очереди есть запросы → они резолвятся (final drain), таск завершается.
10. `test_stop_cancels_leftover_futures` — await'ers не висят после `stop()`.
11. `test_auto_start_on_first_embed` — `embed()` без явного `start()` поднимает цикл.
12. `test_metrics_incremented` — после батча из N: `requests_total` += N, `batches_total` += 1.

Плюс smoke-тест `embed_batch` в `tests/ml/test_triton_embedding.py` (шаг 1).

## Шаг 4 — Верификация

Из `backend/`:

```powershell
uv run pytest tests/ml/test_batcher.py tests/ml/test_triton_embedding.py -q --no-cov
```

(`--no-cov` — чтобы не триггерить глобальный gate `--cov-fail-under=90` на частичном прогоне; финально — полный `uv run pytest -q` если нужно убедиться в покрытии.)

Затем `git diff` — проверить, что изменены только: `triton_client.py` (фикс), тест embed_batch, новый `batcher.py`, новый `test_batcher.py`.

## Затронутые ADR / чек-лист ALPHA

- Чек-лист ALPHA: «[ ] Batching: 32 requests per Triton call» — этот промпт закрывает пункт на уровне кода.
- ADR в репозитории не найдено при exploration; при ответе упомянуть отсутствие изменений ADR.
