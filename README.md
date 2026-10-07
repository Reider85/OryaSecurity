# LLM Security Scanner

![Python](https://img.shields.io/badge/Python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)
![Next.js](https://img.shields.io/badge/Next.js-15-orange)
![License](https://img.shields.io/badge/License-MIT-yellow)

LLM Security Scanner — защитный прокси между вашим AI-приложением и LLM-провайдером. Проверяет входящие промпты на PII/secrets/prompt-injection, исходящие генерации — на утечки.

## 🚀 Quick Start (5 минут)

### 1. Клонирование репозитория

```bash
git clone <repository-url>
cd llm-security-scanner
```

### 2. Запуск через Docker Compose

```bash
cp .env.example .env
docker compose up -d
```

### 3. Проверка работоспособности

```bash
# Проверка бэкенда
curl http://localhost:8000/health

# Проверка фронтенда
curl http://localhost:3000
```

**Доступные сервисы:**
- **Web UI**: http://localhost:3000
- **API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Prometheus**: http://localhost:9090 (опционально)
- **Grafana**: http://localhost:3001 (опционально)

## ✨ Основные возможности

- 🔍 **PII Detection**: SSN, email, российские паспорта
- 🔑 **Secret Detection**: AWS keys, JWT, кредитные карты
- 🛡️ **Prompt Injection Protection**: Блокировка вредоносных промптов
- 🎯 **Decision Cache**: Кэширование решений (TTL 5 минут)
- 📊 **Audit Log**: Полный лог всех сканирований
- 🎨 **Web Dashboard**: Интерфейс для SecOps
- ⚡ **High Performance**: 100 RPS, p99 < 10ms
- 🔒 **API Key Auth**: Безопасная аутентификация

## 🏗️ Архитектура

```
┌─────────────┐    ┌─────────────────┐    ┌─────────────┐
│  AI App     │───▶│ LLM Scanner     │───▶│ LLM Provider│
│ (Next.js)   │    │ (FastAPI + Redis)│    │ (OpenAI API)│
└─────────────┘    └─────────────────┘    └─────────────┘
```

### Компоненты:

- **Backend**: Python 3.12 + FastAPI + PostgreSQL + Redis
- **Frontend**: Next.js 15 + TypeScript + Tailwind CSS + shadcn/ui
- **Database**: PostgreSQL (audit log) + Redis (cache)
- **Monitoring**: Prometheus + Grafana

## 🛠️ Развёртывание

### Вариант 1: Docker Compose (рекомендуется)

```bash
# Полный стек с UI
docker compose --profile ui up -d

# Только бэкенд
docker compose up -d scanner

# С мониторингом
docker compose --profile observability up -d
```

### Вариант 2: Ручная установка

#### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e .
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

#### Frontend

```bash
cd frontend
npm install
npm run dev
```

#### База данных

```bash
# Запуск PostgreSQL
docker run -d --name postgres \
  -e POSTGRES_DB=scanner \
  -e POSTGRES_USER=scanner \
  -e POSTGRES_PASSWORD=scanner \
  -p 5432:5432 postgres:16-alpine

# Запуск Redis
docker run -d --name redis \
  -p 6379:6379 redis:7-alpine
```

## 🔑 Конфигурация

### Переменные окружения

Скопируйте `.env.example` в `.env` и настройте:

```env
# Database
POSTGRES_DB=scanner
POSTGRES_USER=scanner
POSTGRES_PASSWORD=your_secure_password

# Redis
REDIS_URL=redis://localhost:6379/0

# LLM Provider
LLM_PROVIDER_URL=https://api.openai.com/v1/chat/completions
LLM_API_KEY=your_llm_api_key_here

# Security
API_KEY_DEFAULT=your_api_key_here
JWT_SECRET=your_jwt_secret_here

# Cache
CACHE_TTL_SECONDS=300
CACHE_MAX_SIZE=10000

# Performance
RATE_LIMIT_RPS=100
LLM_TIMEOUT_SECONDS=30
```

### Правила сканирования

Правила хранятся в YAML формате в `backend/rules/`:

```yaml
# backend/rules/pii.yaml
rules:
  - id: pii_ssn_us
    name: "US Social Security Number"
    type: regex
    pattern: '\b\d{3}-\d{2}-\d{4}\b'
    severity: high
    action: block
    version: "1.0.0"
```

## 📖 Использование

### 1. Через Web UI

1. Откройте http://localhost:3000
2. Введите API ключ для аутентификации
3. Используйте разделы:
   - **Dashboard**: Основные метрики
   - **Test**: Тестовое сканирование промптов
   - **Audit**: Просмотр логов с фильтрами
   - **Rules**: Редактирование правил
   - **Cache**: Управление кэшем
   - **Config**: Настройки системы

### 2. Через API

```bash
# Сканирование промпта
curl -X POST "http://localhost:8000/scan" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{"prompt": "What is the weather?"}'

# OpenAI совместимый API
curl -X POST "http://localhost:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### 3. Через Python SDK

```python
from llm_security_scanner import ScannerClient

# Синхронный клиент
client = ScannerClient(
    url="http://localhost:8000",
    api_key="your-api-key"
)

result = client.scan("What is the weather?")
print(f"Verdict: {result.verdict}")
print(f"Reason: {result.reason}")

# Асинхронный клиент
import asyncio

async def scan_async():
    async with AsyncScannerClient(
        url="http://localhost:8000",
        api_key="your-api-key"
    ) as client:
        result = await client.scan("Hello world")
        print(result.verdict)

asyncio.run(scan_async())
```

## 🧪 Тестирование

### Запуск тестов

```bash
# Бэкенд
cd backend
python -m pytest --cov=app --cov-report=term-missing --cov-fail-under=90

# Фронтенд
cd frontend
npm run test:e2e
npm run test:e2e:ui
```

### Покрытие тестами

- **Бэкенд**: 90%+ покрытие кода
- **Интеграционные тесты**: PostgreSQL + Redis
- **E2E тесты**: Полный пользовательский сценарий

## 📊 Мониторинг

### Prometheus

Метрики доступны на http://localhost:8000/metrics:

```bash
# Просмотр метрик
curl http://localhost:8000/metrics

# Пример метрик:
# scanner_requests_total{verdict="allow", tenant_id="default"} 1234
# scanner_request_duration_seconds_bucket{le="0.01"} 456
# scanner_cache_hits_total{type="decision"} 789
```

### Grafana

Дашборд по умолчанию: http://localhost:3001

Логин: `admin` / `admin`

## 🔧 Разработка

### Структура проекта

```
llm-security-scanner/
├── backend/                 # FastAPI сканер
│   ├── app/
│   │   ├── api/             # эндпоинты
│   │   ├── core/            # бизнес-логика
│   │   ├── models/          # Pydantic схемы
│   │   └── db/              # БД клиенты
│   ├── tests/               # тесты
│   └── rules/              # правила сканирования
├── frontend/                # Next.js UI
│   ├── app/                 # App Router
│   ├── components/          # UI компоненты
│   └── lib/                 # утилиты
├── docker-compose.yml        # оркестрация
└── README.md
```

### Добавление новых правил

1. Создайте правило в YAML:
```yaml
# backend/rules/custom.yaml
rules:
  - id: custom_pattern
    name: "Custom Detection Pattern"
    type: regex
    pattern: '\bcustom-[a-z]{3}\d{3}\b'
    severity: medium
    action: block
    version: "1.0.0"
```

2. Перезагрузите правила через UI или API:
```bash
curl -X POST "http://localhost:8000/api/v1/rules/reload" \
  -H "Authorization: Bearer your-api-key"
```

## 🚀 Production

### Безопасность

- Используйте HTTPS в production
- Регулярно обновляйте зависимости
- Ограничьте доступ к UI по IP
- Используйте strong API keys
- Настройте rate limiting на reverse proxy

### Производительность

- Оптимизируйте Redis память
- Используйте connection pooling
- Мониторьте latency и error rates
- Масштабируйте горизонтально при необходимости

### Бэкапы

```bash
# PostgreSQL бэкап
docker exec postgres pg_dump -U scanner scanner > backup.sql

# Redis бэкап
docker exec redis redis-cli --rdb /data/dump.rdb > redis_backup.rdb
```

## 📚 Документация

- [API Reference](http://localhost:8000/docs) - Swagger UI
- [Architecture](docs/architecture.md) - Детальная архитектура
- [Roadmap](docs/roadmap.md) - План развития
- [Troubleshooting](docs/troubleshooting.md) - Решение проблем

## 🤝 Contributing

1. Форкните репозиторий
2. Создайте ветку: `git checkout -b feature/amazing-feature`
3. Внесите изменения
4. Добавьте тесты
5. Проверьте покрытие тестов > 90%
6. Сделайте PR

## 📄 License

MIT License - смотрите [LICENSE](LICENSE) файл

## 🆘 Поддержка

Для вопросов и поддержки:
- Создайте issue в GitHub
- Проверьте [FAQ](docs/faq.md)
- Свяжитесь с командой разработки

---

*LLM Security Scanner — защитите ваши AI приложения от утечек данных и атак*