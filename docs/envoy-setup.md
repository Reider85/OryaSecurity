# Envoy Setup (EP-35, Prompt 46)

Envoy как frontend proxy стека ALPHA: TLS termination, маршрутизация
scanner/UI, rate limit 100 RPS per IP, access log в Loki.
Основание: [ADR-0001](../analytics/adr/0001-integration-pattern-reverse-proxy.md)
(Reverse Proxy как primary pattern), ADR-0014 (Deployment Topology).

## Архитектура

```
                        ┌─────────────────────────────────────────────┐
 Клиент (AI-app / UI)   │  envoy  (envoyproxy/envoy:v1.31-latest)     │
                        │                                             │
   http://host:80  ────►│  :80  ── 301 ──► https://host:443           │
                        │                                             │
   https://host:443 ───►│  :443  TLS termination                      │
                        │    certs: /etc/ssl/scanner.{crt,key}        │
                        │    local_ratelimit: 100 RPS per IP          │
                        │    access_log: JSON → stdout                │
                        │                                             │
                        │  Routes:                                    │
                        │    /v1/ /v2/ /api/ /scan                    │
                        │    /metrics /health /docs /redoc ──────────►│──► scanner:8000
                        │    /  ─────────────────────────────────────►│──► ui:3000
                        └─────────────────────────────────────────────┘
                                   │ stdout (JSON access log)
                                   ▼
                             Promtail ──► Loki   LogQL: {app="envoy"} | json
```

- **TLS termination** — self-signed dev-сертификат (`backend/scripts/gen_certs.sh`);
  в production — корпоративный PKI / ACME.
- **Streaming-safe** — route timeout `0s` на `/v1/` и `/v2/` (SSE, EP-07),
  HCM `stream_idle_timeout: 0s`.
- **Rate limit** — `envoy.filters.http.local_ratelimit` + route-действия
  `remote_address`: per-IP token bucket 100 tokens / 1s. Превышение → `429`
  + заголовок `x-local-rate-limit: true`.
- **Access log** — JSON в stdout; Promtail (docker_sd, уже в alpha-стеке)
  цепляет контейнер `envoy` с меткой `app="envoy"`.

## Quick start

```bash
# 1. dev-сертификаты (идемпотентно; --force для пересоздания)
bash backend/scripts/gen_certs.sh

# 2. поднять стек с UI (envoy + scanner + ui + ALPHA-сервисы)
docker compose -f docker-compose.yml -f docker-compose.alpha.yml --profile ui up -d envoy scanner ui

# 3. проверка
curl -I  http://localhost/health        # 301 → https
curl -kI https://localhost/health       # 200 (scanner /health)
curl -kI https://localhost:443/         # 200 (ui)
```

Полный alpha-стек: `docker compose -f docker-compose.yml -f docker-compose.alpha.yml --profile ui up -d`

## Генерация сертификатов

`backend/scripts/gen_certs.sh` пишет в `certs/` (gitignored, см. `certs/.gitignore`):

| Файл | Назначение |
|---|---|
| `certs/scanner.crt` | certificate chain → `/etc/ssl/scanner.crt` |
| `certs/scanner.key` | private key (mode 600) → `/etc/ssl/scanner.key` |

Параметры (env):

```bash
CERT_CN=localhost                # Subject CN
CERT_DAYS=365                    # срок действия
CERT_EXTRA_SANS="DNS:myhost,IP:10.0.0.5"   # доп. SAN (localhost/envoy/scanner/127.0.0.1 всегда)
```

Скрипт пропускает генерацию, если сертификат ещё валиден (>7 дней).
После пересоздания: `docker compose ... restart envoy`.

## Маршрутизация

| Путь | Кластер | Timeout | Комментарий |
|---|---|---|---|
| `/v1/*` | scanner:8000 | 0s (без лимита) | OpenAI-compat, включая SSE-стриминг |
| `/v2/*` | scanner:8000 | 0s (без лимита) | streaming inspector (EP-07) |
| `/api/*` | scanner:8000 | 30s | UI API (audit, decisions, charts, admin) |
| `/scan` | scanner:8000 | 30s | scan endpoint |
| `/metrics` | scanner:8000 | 10s | Prometheus scrape |
| `/health` | scanner:8000 | 5s | health check |
| `/docs`, `/redoc`, `/openapi.json` | scanner:8000 | 10s | Swagger (dev) |
| `/` | ui:3000 | 30s | Next.js Web UI |

## Same-origin режим (опционально)

По умолчанию UI ходит в API напрямую (`NEXT_PUBLIC_API_URL=http://localhost:8000`).
Чтобы весь трафик шёл через Envoy (единый TLS, rate limit, access log):

```bash
NEXT_PUBLIC_API_URL=https://localhost docker compose -f docker-compose.yml \
  -f docker-compose.alpha.yml --profile ui up -d --build ui
```

## Access log → Loki

Примеры LogQL (Grafana Explore):

```logql
{app="envoy"} | json                                  # все запросы к ингрессу
{app="envoy"} | json |= "response_code=\"429\""       # сработал rate limit
{app="envoy"} | json | path="/v2/chat/completions"    # стриминг-трафик
sum by (path) (rate({app="envoy"} | json [5m]))       # RPS по путям
```

Поля JSON: `ts`, `method`, `path`, `authority`, `protocol`, `response_code`,
`response_flags`, `duration_ms`, `client_ip`, `upstream_cluster`,
`request_id`, `user_agent`, ...

## Проверка конфигурации

```bash
# валидация envoy.yaml без запуска стека
docker run --rm -v ./observability/envoy.yaml:/etc/envoy/envoy.yaml:ro \
  envoyproxy/envoy:v1.31-latest --mode validate -c /etc/envoy/envoy.yaml

# admin-интерфейс (проброс 127.0.0.1:9901)
curl -s http://127.0.0.1:9901/stats/prometheus | grep rate_limit
curl -s http://127.0.0.1:9901/config_dump | head
```

## Troubleshooting

| Симптом | Причина / действие |
|---|---|
| `envoy` crashloop, лог «failed to load certificate» | Не запущен `gen_certs.sh` или пустая `certs/` |
| 503 на `/` | ui не запущен: нужен `--profile ui` |
| 503 на `/v1/` | scanner не запущен/не прошёл health check |
| 429 на всех запросах | rate limit: проверить `client_ip` (за NAT все клиенты с одним IP → поднять лимит в `envoy.yaml`) |
| Обрыв SSE через прокси | проверить `timeout: 0s` на маршруте `/v2/` и `stream_idle_timeout: 0s` |
| Логов в Loki нет | перезапустить promtail (docker_sd refresh 5s), проверить `{app="envoy"}` |

## Security notes

- `certs/` в gitignore — self-signed ключи не коммитятся.
- Порты80/443 публикуются на все интерфейсы хоста; admin (9901) — только loopback.
- mTLS между клиентом и ingress — BETA (EP-35.6); на ALPHA — терминация TLS + rate limit.
- В production заменить self-signed сертификат на доверенный (PKI/ACME).

## Related

- `observability/envoy.yaml` — конфигурация Envoy
- `backend/scripts/gen_certs.sh` — генерация dev-сертификатов
- `docker-compose.alpha.yml` — сервис `envoy`
- [ADR-0001](../analytics/adr/0001-integration-pattern-reverse-proxy.md) — Reverse Proxy primary
- [BACKLOG.md](../analytics/BACKLOG.md) — EP-35.1 (Envoy TLS termination)
- [alerting-setup.md](alerting-setup.md) — алертинг поверх того же стека
