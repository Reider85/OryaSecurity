# Alerting Setup (EP-15, Prompt 45)

Настройка PagerDuty и Slack для critical/warning алертов LLM Security Scanner.

## Архитектура

```
Prometheus (rules) → Alertmanager → PagerDuty (critical) / Slack (warning)
    │                    │
    └── Loki (logs) ────┘
```

- Prometheus: 5 базовых алертов из `observability/prometheus-rules.yml`
- Alertmanager: роутинг по severity, ингибит-правила
- PagerDuty: critical алерты (service down, circuit breaker open)
- Slack: warning алерты (высокая задержка, ошибки)
- Loki: логи всех сервисов (app="scanner")

## Алерты (Prometheus Rules)

| Алерт | Условие | Severity | Цель |
|---|---|---|---|
| HighLatency | p99 > 100ms за 5 мин | warning | Slack |
| CircuitBreakerOpen | state=open за 1 мин | critical | PagerDuty |
| AuditWriteFailure | rate > 0 за 1 мин | critical | PagerDuty |
| VectorDBDown | health=fail за 1 мин | critical | PagerDuty |
| MLClassifierDown | health=fail за 1 мин | critical | PagerDuty |

## Роутинг (Alertmanager)

- Critical → PagerDuty
- Warning → Slack #scanner-alerts
- Inhibit: critical алерт подавляет warning того же alertname/component

## Настройка

### 1. PagerDuty Integration Key

1. Зайди в [PagerDuty](https://yourcompany.pagerduty.com/)
2. Создай новый сервис (Services → Add New Service)
3. Выбери "Events API V2"
4. Скопируй Integration Key → вставь в `.env` как `PAGERDUTY_SERVICE_KEY`
5. Настрой severity mapping: `P1` → `critical` (Alertmanager по умолчанию)

### 2. Slack Incoming Webhook

1. Зайди в Slack → [Apps](https://your-org.slack.com/apps)
2. Создай Incoming Webhook:
   - Add → Build → "Incoming Webhooks"
   - Choose channel: `#scanner-alerts`
   - Add to Slack → скопируй Webhook URL
3. Вставь URL в `.env` как `SLACK_WEBHOOK_URL`

### 3. Environment Variables

Добавь в `.env`:

```bash
# Alerting (Prompt 45)
PAGERDUTY_SERVICE_KEY=ваш_integration_key
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

### 4. Запуск

```bash
docker compose --profile observability up -d
```

## Проверка

### 1. Проверка конфига

```bash
docker run --rm \
  -v "$(pwd)/observability/alertmanager.yml:/tmp/alertmanager.yml.tmpl:ro" \
  -v "$(pwd)/observability/alertmanager-entrypoint.sh:/entrypoint.sh:ro" \
  -e PAGERDUTY_SERVICE_KEY=test \
  -e SLACK_WEBHOOK_URL=https://example.com \
  prom/alertmanager:v0.27.0 amtool check-config /tmp/alertmanager.yml.tmpl
```

### 2. Тестовый алерт

```bash
curl -X POST http://localhost:9090/api/v1/alerts \
  -H 'Content-Type: application/json' \
  -d '{
    "alerts": [{
      "labels": {
        "alertname": "TestAlert",
        "severity": "critical",
        "component": "scanner"
      },
      "annotations": {
        "summary": "Test alert from curl"
      }
    }]
  }'
```

Проверь:
- PagerDuty: новый инцидент
- Slack: сообщение в #scanner-alerts
- Alertmanager UI: http://localhost:9093

### 3. Silences

```bash
# Создать silence (Alertmanager UI или amtool)
amtool silence create \
  --author="dev" \
  --matcher="alertname=TestAlert" \
  --duration="1h"
```

## Траблшутинг

### Уведомления не приходят

1. Проверь рендер конфига:
   ```bash
   docker exec alertmanager cat /tmp/alertmanager.yml
   ```
2. Проверь connectivity:
   ```bash
   curl -I $SLACK_WEBHOOK_URL
   curl -H "Authorization: Token $PAGERDUTY_SERVICE_KEY" -X POST https://events.pagerduty.com/v2/enqueue
   ```
3. Логи Alertmanager:
   ```bash
   docker logs alertmanager
   ```

### Конфиг не проходит валидацию

1. Проверь синтаксис шаблона (`observability/alertmanager.yml`)
2. Проверь env-переменные в `.env`
3. Проверь спецсимволы в ключах (особенно `/` в Slack URL)

### Алерты не роутятся

1. Проверь severity метки в алертах
2. Проверь inhibit rules в Alertmanager UI
3. Проверь receiver names (`pagerduty`/`slack`)

## Безопасность

- Ключи хранятся только в `.env` (не в репозитории)
- `.env` добавлен в `.gitignore`
- Entrypoint использует `set -eu` — остановится при пустых ключах
- Alertmanager не пишет ключи в логи (секреты redacted)

## Рендеринг конфига

Alertmanager не поддерживает env-переменные в конфиге. Entrypoint:
1. Читает `observability/alertmanager.yml.tmpl`
2. Подставляет `${PAGERDUTY_SERVICE_KEY}` и `${SLACK_WEBHOOK_URL}`
3. Записывает отрендеренный конфиг в `/tmp/alertmanager.yml`
4. Запускает Alertmanager с этим конфигом

При пустых ключах используются плейсхолдеры — стек стартует, но уведомления не уходят.