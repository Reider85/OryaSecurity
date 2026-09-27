from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "LLM Security Scanner"
    app_version: str = "0.1.0"
    debug: bool = False

    api_keys: list[str] = ["dev-key-12345"]

    cache_ttl_seconds: int = 300
    cache_max_size: int = 10000

    pdp_default_action: str = "allow"
    pdp_block_on_severity: list[str] = ["high", "critical"]

    llm_provider_url: str = "http://localhost:8001"
    llm_timeout_seconds: int = 30

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/scanner"
    redis_url: str = "redis://localhost:6379/0"

    log_level: str = "INFO"

    model_config = {"env_prefix": "SCANNER_"}


settings = Settings()
