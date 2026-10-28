# HashiCorp Vault Setup (EP-13, Prompt 31)

Развёртывание Vault для PII tokenization в ALPHA-стеке
(ADR-0007: «Tokenization PII/secrets через Vault»).

## Архитектура

```
docker-compose.alpha.yml (основной стек)
┌─────────────────────────┐        ┌──────────────────────────────────┐
│ scanner                 │        │ Vault HA: 3 ноды, Raft           │
│  VaultClient (async     │  HTTP  │                                  │
│  httpx + circuit        │───────▶│  vault1 :8200 (active leader)    │
│  breaker)               │        │  vault2 :8210 → :8200 (standby)  │
│  SCANNER_VAULT_URL      │        │  vault3 :8220 → :8200 (standby)  │
│  SCANNER_VAULT_TOKEN    │        │  raft storage, retry_join        │
└─────────────────────────┘        └──────────────────────────────────┘
                                             │
                                  backend/scripts/vault_init.sh
                                  init (Shamir 5/3) → unseal →
                                  enable engine → create key
                                             │
                     ┌───────────────────────┴───────────────────────┐
                     │ tokenization (Vault Enterprise)  — preferred  │
                     │ transit + convergent_encryption  — fallback   │
                     │ mount: $VAULT_MOUNT (default "tokenization")  │
                     └───────────────────────────────────────────────┘
                                  ▲
                                  │ сохраняются (gitignored, 600)
                     backend/scripts/.vault-secrets/
                       unseal-keys.txt   root-token.txt   vault.env
```

Dev-окружение (одиночная нода, ephemeral):

```bash
docker compose -f docker-compose.yml -f docker-compose.alpha.yml --profile dev up -d vault
# auto-init + auto-unseal, root token = VAULT_DEV_ROOT_TOKEN (default: dev-token)
```

Внимание: dev-нода занимает `:8200` — **не запускай её одновременно с vault1-3**
(port conflict). Сканеру в dev укажи URL наружу (compose override указывает
на vault1, поэтому переопредели в `.env`):

```bash
docker compose -f docker-compose.yml -f docker-compose.alpha.yml --profile dev up -d vault scanner
# в .env: SCANNER_VAULT_URL=http://host.docker.internal:8200
```

## Quick start (HA-кластер)

```bash
# 1. поднять 3 ноды
docker compose -f docker-compose.yml -f docker-compose.alpha.yml up -d vault1 vault2 vault3

# 2. init + unseal + включить engine (идемпотентно, можно повторять)
bash backend/scripts/vault_init.sh

# 3. положить токен в окружение сканера (файл сгенерирован скриптом)
export VAULT_TOKEN="$(cat backend/scripts/.vault-secrets/root-token.txt)"

# 4. поднять/перезапустить сканер
docker compose -f docker-compose.yml -f docker-compose.alpha.yml up -d scanner

# 5. джоба TTL/auto-cleanup (Prompt 34) — ротация ключа каждые 5 минут
docker compose -f docker-compose.yml -f docker-compose.alpha.yml up -d vault-cleanup
```

## Что делает `vault_init.sh`

| Шаг | Действие | API |
|---|---|---|
| 1 | Ожидание доступности всех нод | `GET /v1/sys/health` (200/429/501/503) |
| 2 | Init первой ноды (Shamir: 5 shares, threshold 3) | `POST /v1/sys/init` |
| 3 | Unseal первой ноды (3 ключа) → она бутстрапит Raft и становится лидером | `PUT /v1/sys/unseal` |
| 4 | Unseal остальных нод (`retry_join` в конфиге подтягивает их в кластер) | `PUT /v1/sys/unseal` |
| 5 | Ожидание активного лидера | `GET /v1/sys/leader` |
| 6 | Включение secrets engine на `$VAULT_MOUNT` | `POST /v1/sys/mounts/{mount}` |
| 7 | Создание ключа шифрования (идемпотентно; + `min_decryption_version=1`, Prompt 34) | `POST /v1/{mount}/keys/{key}` |

Опции: `--skip-engine` (только init/unseal), `--force-engine` (пересоздать mount),
`--help`. Переменные читается из окружения, затем из `<repo>/.env`.

### Engine: tokenization или transit?

Tokenization secrets engine — **Enterprise/HCP-only** feature. Скрипт сначала
пробует его включить, при отказе (open-source Vault) — fallback на **transit с
convergent encryption**:

| | tokenization | transit (fallback) |
|---|---|---|
| Формат токена | `vault-tokenization-...` (opaque) | `vault:v1:...` (ciphertext) |
| Детерминизм | одинаковый plaintext → одинаковый токен | одинаковый plaintext+context → одинаковый шифртекст |
| Context/tenant | встроен (namespace per tenant) | `context` = base64(tenant) |
| Нужен | Vault Enterprise лицензия | open-source Vault |

Клиент (`VaultClient`, `engine="auto"`) определяет тип автоматически по
`GET /v1/sys/mounts/{mount}` — конфиг сканера не зависит от варианта.
Mount path одинаков в обоих случаях (`$VAULT_MOUNT`, default `tokenization`).

## TTL и auto-cleanup токенов (Prompt 34)

Vault не умеет ставить TTL на отдельные PII-токены (ни tokenization, ни
transit) — срок жизни задаётся **версией ключа**:

1. `POST /v1/{mount}/keys/{key}/rotate` — новая версия ключа, все дальнейшие
   `encode` идут в неё;
2. `min_decryption_version` (baseline из промпта:
   `vault write tokenization/keys/default allowed_operations=... min_decryption_version=1`)
   запрещает decrypt старых версий → токены, отчеканенные под ними, перестают
   декодироваться = фактически удалены.

Скрипт `backend/scripts/vault_cleanup.py` каждые `VAULT_TOKEN_TTL_SECONDS`
(по умолчанию 300 с = «5 минут» промпта) выполняет ротацию и поднимает
`min_decryption_version = latest - VAULT_TOKEN_TTL_GRACE_VERSIONS` (по
умолчанию 1):

| | |
|---|---|
| In-flight запросы/стримы | переживают ротацию — предыдущая версия ключа ещё декодируется |
| TTL токена | `(interval, 2*interval]` — **5–10 минут** при 300 с |
| Протухший токен в ответе | inbound fail-open (Prompt 33): остаётся плейсхолдер `<SSN_T_...>` — не блок и не утечка PII |
| `min_decryption_version` | монотонный, никогда не опускается — перезапуск джобы не «оживляет» мёртвые токены |

### Запуск

```bash
# alpha-стек: сервис vault-cleanup крутит --loop автоматически
docker compose -f docker-compose.yml -f docker-compose.alpha.yml up -d vault-cleanup

# один проход (cron/systemd/ручной)
export VAULT_TOKEN="$(cat backend/scripts/.vault-secrets/root-token.txt)"
python backend/scripts/vault_cleanup.py --once            # exit 0 = ок/пропуск
python backend/scripts/vault_cleanup.py --once --dry-run  # без записи в Vault

# dev-нода (--profile dev): vault1 не поднимается, URL указываем вручную
SCANNER_VAULT_URL=http://127.0.0.1:8200 SCANNER_VAULT_TOKEN=dev-token \
  python backend/scripts/vault_cleanup.py --once
```

Exit-коды: `0` — успех или пропуск (неподдерживаемый engine/rotate endpoint),
`1` — ошибка (`--once`), `2` — Vault не сконфигурирован (`SCANNER_VAULT_URL`
пуст). В `--loop` транзиентные ошибки логируются и повторяются — джоба не падает.

| Env var | Default | Описание |
|---|---|---|
| `SCANNER_VAULT_TOKEN_TTL_SECONDS` | `300` | интервал ротации = TTL-окно |
| `SCANNER_VAULT_TOKEN_TTL_GRACE_VERSIONS` | `1` | сколько версий ключа остаётся декодируемыми после ротации |

### Проверка

```bash
# версии ключа: latest_version / min_decryption_version
curl -s -H "X-Vault-Token: $VAULT_TOKEN" $VAULT/v1/tokenization/keys/default \
  | jq '.data.latest_version, .data.min_decryption_version'

# ручная ротация
curl -s -X POST -H "X-Vault-Token: $VAULT_TOKEN" \
  $VAULT/v1/tokenization/keys/default/rotate

# логи джобы (в alpha-стеке)
docker compose -f docker-compose.yml -f docker-compose.alpha.yml logs -f vault-cleanup
```

Автотесты:

```bash
# unit (мок transport, без Vault)
pytest tests/core/test_vault_ttl.py -m "not integration"

# integration (нужен живой нода; ротирует ключ, min_version восстанавливается)
SCANNER_TEST_VAULT_URL=http://127.0.0.1:8200 pytest tests/integration/test_vault_ttl.py -m vault
```

## Креденшелы (unseal keys / root token)

- Пишутся в `backend/scripts/.vault-secrets/` — **в .gitignore, никогда не коммить**;
  `chmod 600` применяется там, где файловая система это поддерживает.
- `vault.env` — готовый фрагмент: `VAULT_TOKEN=...` (добавить в `<repo>/.env`
  или export перед `docker compose up`).
- Dev-нода (`--profile dev`) использует `VAULT_DEV_ROOT_TOKEN` (default
  `dev-token`) — только для локальной разработки.
- Re-init (потеря ключей): удалить volume ноды (`docker compose ... down -v`
  для `vault*_data`) и запустить скрипт заново. **Все токены старого
  кластера становятся невалидными.**

## Настройка сканера

| Env var | Default | Описание |
|---|---|---|
| `SCANNER_VAULT_URL` | `""` (выкл.) | Базовый URL Vault; пусто = интеграция отключена |
| `SCANNER_VAULT_TOKEN` | `""` | Root/app токен (`X-Vault-Token`) |
| `SCANNER_VAULT_MOUNT` | `tokenization` | Path secrets engine |
| `SCANNER_VAULT_KEY_NAME` | `default` | Ключ внутри engine (в BETA — per-tenant) |
| `SCANNER_VAULT_ENGINE` | `auto` | `auto` \| `tokenization` \| `transit` |
| `SCANNER_VAULT_NAMESPACE` | `""` | Vault Enterprise namespace (ADR-0011, BETA) |
| `SCANNER_VAULT_TIMEOUT_MS` | `200` | HTTP timeout |
| `SCANNER_VAULT_BREAKER_FAIL_MAX` | `5` | Circuit breaker: ошибок до открытия |
| `SCANNER_VAULT_BREAKER_RESET_TIMEOUT` | `30` | Circuit breaker: секунд в open |
| `SCANNER_VAULT_TOKEN_TTL_SECONDS` | `300` | TTL/интервал ротации ключа (Prompt 34) |
| `SCANNER_VAULT_TOKEN_TTL_GRACE_VERSIONS` | `1` | Версий ключа, остающихся декодируемыми после ротации |

`docker-compose.alpha.yml` передаёт `SCANNER_VAULT_URL=http://vault1:8200`
и `SCANNER_VAULT_TOKEN=${VAULT_TOKEN:-}` автоматически.

### Использование в коде

```python
from app.core.redaction import get_vault_client

client = get_vault_client()
if client is not None:
    enc = await client.encode("123-45-6789", context=tenant_id)
    if enc.ok:
        token = enc.value            # → "<SSN_T_42>" формирует redactor (Prompt 32)
    dec = await client.decode(token, context=tenant_id)
    # dec.value == "123-45-6789"
```

Клиент **никогда не бросает исключения**: при недоступности Vault возвращается
`VaultResult(ok=False, error=...)`, circuit breaker открывается после
`fail_max` ошибок и коротко-замыкает вызовы. Fail-closed политика для PII —
ответственность redactor (Prompt 32) / CB (Prompt 38, ADR-0008).

## Проверка

```bash
export VAULT_TOKEN="$(cat backend/scripts/.vault-secrets/root-token.txt)"
VAULT=http://localhost:8200

# health: 200 = active+unsealed, 429 = standby, 503 = sealed
curl -s $VAULT/v1/sys/health | jq '{initialized, sealed, standby}'

# engine type (tokenization | transit)
curl -s -H "X-Vault-Token: $VAULT_TOKEN" $VAULT/v1/sys/mounts/tokenization | jq .data.type

# round-trip (transit; для tokenization заменить encrypt→encode, plaintext→token)
P=$(printf '123-45-6789' | base64)
T=$(curl -s -H "X-Vault-Token: $VAULT_TOKEN" -H 'Content-Type: application/json' \
  -X POST $VAULT/v1/tokenization/encrypt/default \
  -d "{\"plaintext\":\"$P\",\"convergent_encryption\":true,\"context\":\"$(printf tenant | base64)\"}" \
  | jq -r .data.ciphertext)
curl -s -H "X-Vault-Token: $VAULT_TOKEN" -H 'Content-Type: application/json' \
  -X POST $VAULT/v1/tokenization/decrypt/default \
  -d "{\"ciphertext\":\"$T\",\"context\":\"$(printf tenant | base64)\"}" | jq -r .data.plaintext | base64 -d
```

Автотесты:

```bash
# unit (мок transport, без Vault)
pytest tests/core/test_vault_client.py -m "not integration"

# integration (нужен живой нода)
docker compose --profile dev up -d vault
bash backend/scripts/vault_init.sh --skip-engine   # либо полный прогон на HA
SCANNER_TEST_VAULT_URL=http://127.0.0.1:8200 pytest tests/integration/test_vault.py -m vault
```

## Troubleshooting

| Симптом | Причина / решение |
|---|---|
| `health 503` на всех нодах | Нода sealed — запустить `vault_init.sh` (шаги 3-4) |
| `health 501` | Нода не инициализирована — `vault_init.sh` (шаг 2) |
| `only 0/3 node(s) reachable` | Кластер не поднят: `docker compose -f docker-compose.alpha.yml up -d vault1 vault2 vault3` |
| `unseal of ... reset to progress=0` | Unseal-ключи не подходят (re-init) — удалить `vault*_data` volumes и переинициализировать |
| `tokenization unavailable (HTTP 4xx)` → transit | Норма для open-source Vault (лог WARN в скрипте) |
| `vault_engine_unavailable` в сканере | Mount не создан — выполнить `vault_init.sh` без `--skip-engine` |
| `permission denied` в сканере | Неверный/пустой `SCANNER_VAULT_TOKEN` — подставить из `.vault-secrets/vault.env` |
| `circuit_open` в логах сканера | Vault недоступен дольше 5 ошибок подряд; восстановится сам после `reset_timeout` (30s) |
| `vault_cleanup_failed` / `rotation_forbidden` | Токену нужен `update` на `{mount}/keys/{key}` и `update` на `{mount}/keys/{key}/rotate` |
| `vault_cleanup_skipped: rotation_unsupported` | Endpoint rotate отсутствует (сборка Tokenization без rotate API) — TTL не работает, проверьте версию Vault |
| `<SSN_T_...>` остался в ответе | Норма для протухшего токена (TTL 5–10 мин, Prompt 34) — inbound fail-open |

## Security notes

- `tls_disable = true` на ALPHA — трафик внутри docker-сети; наружу Vault не
  торчит (ноды 2/3 — только loopback). TLS termination — Envoy (EP-35, промпт 46).
- Root token в env сканера — dev-практика. Для продакшена: отдельный
  AppRole/token с policy только на `{mount}/encode/*`, `{mount}/decode/*`,
  `sys/mounts/{mount}` (read); для джобы `vault_cleanup.py` дополнительно
  `update` на `{mount}/keys/{key}` и `{mount}/keys/{key}/rotate`.
- Unseal-ключи хранятся только в `.vault-secrets/` + у оператора; Shamir 5/3 —
  распределение ключей у SecOps.
- TTL/auto-cleanup токенов — реализовано (см. «TTL и auto-cleanup токенов»);
  per-tenant namespaces — BETA (ADR-0011).
- Снапшоты Raft (backup) — Prompt 15/BETA, см. `docs/qdrant-backup.md` для S3-паттерна.

## Related

- **ADR-0007** — Tokenization PII/secrets через Vault (основной ADR)
- **ADR-0008** — Circuit breaker fallback (fail-closed для Vault, Prompt 38)
- **ADR-0011** — Multi-tenancy: per-tenant namespaces (BETA)
- Prompts: 31 (этот файл) → 32 redactor → 33 detokenizer → 34 TTL → 35 Presidio
