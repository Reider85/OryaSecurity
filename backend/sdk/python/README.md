# LLM Security Scanner Python SDK

Python SDK для LLM Security Scanner — инлайн-сервиса безопасности между вашим AI-приложением и LLM-провайдером.

## 🚀 Установка

```bash
pip install llm-security-scanner
```

Для разработки:

```bash
git clone <repository-url>
cd backend/sdk/python
pip install -e ".[dev]"
```

## 📋 Требования

- Python 3.8+
- httpx >= 0.28.0
- requests >= 2.31.0
- pydantic >= 2.10.0

## 🎯 Основные возможности

- 🔍 **Сканирование промптов**: Проверка на PII, секреты и prompt injection
- 🔄 **Проксирование чатов**: Безопасная передача запросов через LLM-провайдер
- 🏷️ **Метки вердиктов**: Автоматическое добавление заголовков X-Scanner-Verdict
- 💾 **Кеширование**: Оптимизация повторяющихся запросов
- 📊 **Метрики**: Интеграция с Prometheus для мониторинга
- 🔐 **Аутентификация**: Поддержка API-key авторизации
- ⚡ **Асинхронная поддержка**: Полная поддержка async/await

## 📖 Быстрый старт

### Синхронное использование

```python
from llm_security_scanner import ScannerClient

# Инициализация клиента
client = ScannerClient(
    url="http://localhost:8000",
    api_key="your-api-key"
)

# Сканнинг промпта
result = client.scan("What is the weather?")
print(f"Вердикт: {result.verdict}")
print(f"Причина: {result.reason}")
print(f"Затрачено времени: {result.latency_ms}ms")

# Проксирование чата
response = client.proxy_chat([
    {"role": "user", "content": "Hello, how are you?"}
])
print(f"Ответ: {response.choices[0].message.content}")

# Использование с контекстным менеджером
with ScannerClient("http://localhost:8000", "your-api-key") as client:
    result = client.scan("Test prompt")
```

### Асинхронное использование

```python
import asyncio
from llm_security_scanner import AsyncScannerClient

async def main():
    client = AsyncScannerClient(
        url="http://localhost:8000",
        api_key="your-api-key"
    )
    
    # Сканнинг промпта
    result = await client.scan("What is the weather?")
    print(f"Вердикт: {result.verdict}")
    
    # Проксирование чата
    response = await client.proxy_chat([
        {"role": "user", "content": "Hello, how are you?"}
    ])
    print(f"Ответ: {response.choices[0].message.content}")
    
    # Закрытие клиента
    await client.close()

asyncio.run(main())
```

## 📚 API Reference

### ScannerClient

Основной класс для синхронного взаимодействия.

#### Конструктор

```python
ScannerClient(
    url: str,
    api_key: str,
    timeout: float = 30.0,
    verify_ssl: bool = True
)
```

**Параметры:**
- `url`: Базовый URL сканера (например, "http://localhost:8000")
- `api_key`: API ключ для аутентификации
- `timeout`: Таймаут запроса в секундах
- `verify_ssl`: Проверять SSL сертификаты

#### Методы

##### scan()

```python
scan(
    prompt: str,
    tenant_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> ScanResult
```

Сканирует промпт на предмет PII и секретов.

**Параметры:**
- `prompt`: Текст для сканирования
- `tenant_id`: Идентификатор клиента (опционально)
- `metadata`: Дополнительные метаданные (опционально)

**Возвращает:** `ScanResult`

##### proxy_chat()

```python
proxy_chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    top_p: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    stop: Optional[Union[str, List[str]]] = None,
    stream: bool = False,
    **kwargs: Any
) -> ChatCompletionResponse
```

Проксирует запрос к LLM через сканер безопасности.

**Параметры:**
- `messages`: Список сообщений с ролями и контентом
- `model`: Модель для генерации (опционально)
- `temperature`: Температура выборки (0.0-2.0)
- `max_tokens`: Максимальное количество токенов
- `top_p`: Параметр nucleus sampling
- `frequency_penalty`: Штраф за частые токены (-2.0-2.0)
- `presence_penalty`: Штраф за присутствие токенов (-2.0-2.0)
- `stop`: Последовательности остановки
- `stream`: Потоковая передача (не поддерживается в MVP)
- `**kwargs`: Дополнительные параметры OpenAI API

**Возвращает:** `ChatCompletionResponse`

### AsyncScannerClient

Асинхронная версия клиента с тем же API.

#### Конструктор

```python
AsyncScannerClient(
    url: str,
    api_key: str,
    timeout: float = 30.0,
    verify_ssl: bool = True
)
```

#### Методы

Методы аналогичны синхронной версии, но являются асинхронными и возвращают `await`:

- `scan()` → `await scan()`
- `proxy_chat()` → `await proxy_chat()`

### Модели

#### ScanResult

```python
class ScanResult(BaseModel):
    verdict: str          # "allow" или "block"
    reason: str           # Причина вердикта
    latency_ms: float     # Затраченное время в мс
    request_id: str        # Идентификатор запроса
    rules_matched: List[RuleMatch]  # Список совпавших правил
    cache_hit: bool       # Попадание в кеш
```

#### ChatCompletionResponse

```python
class ChatCompletionResponse(BaseModel):
    id: str                           # Идентификатор ответа
    object: str                       # "chat.completion"
    created: int                      # Unix timestamp
    model: str                        # Использованная модель
    choices: List[ChatCompletionChoice]  # Варианты ответа
    usage: ChatCompletionUsage        # Использование токенов
```

#### RuleMatch

```python
class RuleMatch(BaseModel):
    rule_id: str        # ID правила
    rule_name: str      # Название правила
    value_hash: str    # Хэш значения
    position: List[int] # Позиция в тексте [start, end]
    severity: str       # Уровень серьезности
    action: str         # Действие: allow, block, log_only
```

### Обработка ошибок

SDK предоставляет несколько типов исключений:

```python
from llm_security_scanner import (
    ScannerError,
    AuthenticationError,
    RateLimitError,
    ScannerAPIError
)

try:
    result = client.scan("Test prompt")
except AuthenticationError:
    print("Неверный API ключ")
except RateLimitError as e:
    print(f"Превышен лимит запросов. Повторите через {e.retry_after} секунд")
except ScannerAPIError as e:
    print(f"Ошибка API: {e.status_code} - {e.response_text}")
```

## 🔧 Конфигурация

### Переменные окружения

SDK использует следующие переменные окружения (опционально):

```bash
# URL сканера
SCANNER_URL=http://localhost:8000

# Таймаут запросов
SCANNER_TIMEOUT=30

# Проверка SSL
SCANNER_VERIFY_SSL=true
```

### Заголовки запросов

SDK автоматически добавляет следующие заголовки:
- `Authorization: Bearer <api_key>`
- `Content-Type: application/json`
- `User-Agent: llm-security-scanner-python/0.1.0`

## 🧪 Тестирование

Запуск тестов:

```bash
# Все тесты
pytest

# С покрытием
pytest --cov=llm_security_scanner

# Конкретный файл
pytest tests/test_client.py

# Асинхронные тесты
pytest -m asyncio
```

### Моки для тестирования

Для тестирования можно использовать `respx`:

```python
import respx
import httpx

with respx.mock:
    respx.post("http://localhost:8000/scan").mock(
        return_value=httpx.Response(
            status_code=200,
            json={
                "verdict": "allow",
                "reason": "No issues",
                "latency_ms": 25.5,
                "request_id": "123e4567-e89b-12d3-a456-426614174000",
                "rules_matched": [],
                "cache_hit": False
            }
        )
    )
    
    result = client.scan("Test prompt")
```

## 📊 Интеграция с мониторингом

SDK добавляет следующие метрики в заголовки ответов:

- `X-Scanner-Verdict`: allow/block
- `X-Scanner-Reason`: Краткая причина
- `X-Scanner-Latency-Ms`: Затраченное время
- `X-Scanner-Request-Id`: ID запроса
- `X-Scanner-Cache`: HIT/MISS

## 🛡️ Безопасность

- API ключи передаются только через HTTPS
- Все запросы имеют таймауты
- Поддержка проверки SSL сертификатов
- Логирование ошибок без утечки чувствительных данных

## 📄 Лицензия

MIT License - см. файл LICENSE

## 🤝 Вклад

Вклад приветствуется! Пожалуйста, создайте issue или pull request.

## 📞 Поддержка

- Issues: [GitHub Issues](https://github.com/llm-security-scanner/sdk-python/issues)
- Документация: [Wiki](https://github.com/llm-security-scanner/sdk-python/wiki)

---

**Примечание**: Этот SDK предназначен для работы с LLM Security Scanner версии 0.1.0. Для более новых версий может потребоваться обновление.