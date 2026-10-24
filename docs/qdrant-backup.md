# Qdrant Backup/Restore — snapshots → S3 (MinIO)

Настройка snapshot-бэкапов Qdrant каждые 6 часов в S3-совместимое хранилище
(MinIO on-prem) и восстановление из них (Prompt 15 / EP-09, см.
[ADR-0005](../analytics/adr/0005-vector-db-qdrant.md), BACKLOG EP-38).

## Структура файлов

```
backend/scripts/
├── qdrant_backup.sh          # cron: create → download → upload → retention
└── qdrant_restore.sh         # list → download → recover → verify

docker-compose.alpha.yml      # services: minio, minio-init;
                              # host ports 127.0.0.1:6343/6353 для qdrant2/3
.env.example                  # блок MinIO / S3 / QDRANT_URLS
docs/qdrant-backup.md         # этот файл
```

Зависимости скриптов: `curl`, `jq`, AWS CLI v2 (`aws`).

## Как это устроено

`qdrant_backup.sh` на каждом запуске (run):

1. `POST /collections/{collection}/snapshots` — создаёт snapshot **на каждой
   ноде** из `QDRANT_URLS` (шаг 1 из промпта).
2. `GET /collections/{c}/snapshots/{name}` — скачивает tar-архив (шаг 2).
3. `aws s3 cp` в `s3://<bucket>/<prefix>/<collection>/<run_id>/<node>.snapshot`
   (шаг 3). `run_id` — UTC-таймстемп вида `20261024T120000Z`, один на все ноды.
4. `DELETE /collections/{c}/snapshots/{name}` — удаляет snapshot с ноды,
   освобождая диск (снимок — лишь копия, данные коллекции не трогаются).
5. Retention: в S3 хранятся последние `SNAPSHOT_KEEP` (7) **run'ов**
   (шаг 4). Один run при cron `0 */6 * * *` ≈ 42 часа истории.

Layout в S3:

```
s3://qdrant-backups/qdrant/snapshots/
└── prompt_attacks/
    ├── 20261024T060000Z/
    │   ├── localhost-6333.snapshot
    │   ├── localhost-6343.snapshot
    │   └── localhost-6353.snapshot
    └── 20261024T120000Z/
        └── ...
```

При ошибке S3-загрузки локальные копии не удаляются — скрипт печатает путь
(`/tmp/...`) и завершается с кодом 1.

## Кластерные особенности (важно)

Collection-level snapshot в Qdrant содержит **только данные той ноды**, на
которой создан (документация Qdrant: в распределённом деплое снимать нужно
каждую ноду отдельно). Поэтому:

- `QDRANT_URLS` перечисляет **все ноды** кластера; для alpha-стека это
  `qdrant1` (6333), `qdrant2` (6343), `qdrant3` (6353). Host-порты 6343/6353
  проброшены только на loopback (`127.0.0.1`) — внешний доступ к API нод не
  нужен.
- Restore загружает **все** `.snapshot` выбранного run — иначе коллекция
  восстановится без части шардов.
- Dev single-node (`--profile dev`): достаточно
  `QDRANT_URLS=http://localhost:6333`.

## MinIO

Сервисы в `docker-compose.alpha.yml`:

- `minio` — S3 API на `localhost:9000`, консоль на `localhost:9001`,
  volume `minio_data`. Healthcheck в образе отсутствует (нет curl/nc) —
  готовность проверяет `minio-init`.
- `minio-init` — идемпотентно создаёт bucket
  (`mc mb --ignore-existing`), ждёт MinIO циклом `mc alias set ...`.

```bash
docker compose -f docker-compose.yml -f docker-compose.alpha.yml up -d minio minio-init
```

Dev-креды (`minioadmin/minioadmin`) задаются в `.env.example`; **в production
обязательно сменить** `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` и
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`. Для внешнего S3 задайте
`S3_ENDPOINT` (или уберите его для AWS S3) и соответствующие креды —
`aws` CLI использует стандартную цепочку credentials.

## Переменные окружения

| Переменная | Default | Описание |
|---|---|---|
| `QDRANT_URLS` | `http://localhost:6333` | Ноды через запятую (снимать все) |
| `QDRANT_URL` | — | Fallback, если `QDRANT_URLS` не задан |
| `QDRANT_COLLECTION` | `prompt_attacks` | Имя коллекции |
| `S3_ENDPOINT` | `http://localhost:9000` | Пусто = реальный AWS S3 |
| `S3_BUCKET` | `qdrant-backups` | Bucket (создаёт `minio-init`) |
| `S3_PREFIX` | `qdrant/snapshots` | Префикс ключей в bucket |
| `SNAPSHOT_KEEP` | `7` | Сколько run'ов хранить в S3 |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | dev | Креды `aws` CLI |

Скрипты читают переменные окружения, затем `<repo>/.env` (без перезаписи
уже заданных).

## Cron (каждые 6 часов)

```cron
0 */6 * * * cd /path/to/OryaSecurity && bash backend/scripts/qdrant_backup.sh >> /var/log/qdrant-backup.log 2>&1
```

Exit codes: `0` — успех, `1` — ошибка (в логе видна причина). Лог содержит
`[qdrant-backup] ...` — удобно для grep/алертов.

## Ручной запуск

```bash
# обычный backup
bash backend/scripts/qdrant_backup.sh

# фиксированный run id (для повтора/теста) и без retention
bash backend/scripts/qdrant_backup.sh --run-id manual-01 --no-cleanup

# список что уже лежит в S3
bash backend/scripts/qdrant_restore.sh --list
```

## Restore

```bash
# план без изменений
bash backend/scripts/qdrant_restore.sh --dry-run

# последний run (с подтверждением)
bash backend/scripts/qdrant_restore.sh

# конкретный run, без вопросов (для automation)
bash backend/scripts/qdrant_restore.sh --run 20261024T120000Z --yes
```

Что делает:

1. Списывает run'ы в S3, выбирает последний (или `--run`).
2. Скачивает все `<node>.snapshot` run'а.
3. Каждый снимок отправляет на **соответствующую ноду**
   `POST /collections/{c}/snapshots/upload?priority=<priority>`
   (multipart). Если нода из имени файла недоступна — fallback на первую
   из `QDRANT_URLS`.
4. Проверяет результат: `GET /collections/{c}` → `status`, `points_count`,
   `shards_count`.

Коллекция создаётся автоматически, если её нет. Восстановление в
**существующую** коллекцию перезаписывает данные восстанавливаемых шардов —
поэтому нужен подтверждающий prompt (или `--yes`).

`--priority` (default `snapshot`):

- `snapshot` — данные снимка побеждают (правильный выбор для restore;
  дефолт Qdrant `replica` на пустой коллекции дал бы 0 точек);
- `replica` — побеждают существующие данные;
- `no_sync` — без синхронизации (только для специальных сценариев).

## Совместимость версий

Snapshot восстанавливается только на Qdrant **той же minor-версии или
следующей** (правило Qdrant). Стэк зафиксирован на `qdrant/qdrant:v1.12.0` —
при апгрейде сначала проверьте restore на staging.

## Troubleshooting

| Симптом | Причина / действие |
|---|---|
| `aws CLI not found` / `jq not found` | Установить `aws` v2 и `jq` на хост cron |
| `Unable to locate credentials` | Заданы `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` (см. `.env`) |
| `cannot create snapshot on http://localhost:6343` | Нода не поднята или не нужна — сузить `QDRANT_URLS` (dev: одна нода) |
| `no backup runs found` | Бакет пуст — запустить `qdrant_backup.sh`; проверить `docker compose logs minio-init` |
| Локальные файлы в `/tmp/...` | Ошибка S3-загрузки — копии сохранены, можно догрузить вручную `aws s3 cp` |
| На ноде остались `*.snapshot` | Неудалённый снимок: `GET /collections/{c}/snapshots`, затем `DELETE .../snapshots/{name}` |
| Restore: `points=0` | Загружен не весь run (часть нод) — смотреть вывод скрипта, `--list` |
