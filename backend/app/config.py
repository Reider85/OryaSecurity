from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "LLM Security Scanner"
    app_version: str = "0.1.0"
    debug: bool = False

    api_keys: list[str] = ["dev-key-12345"]
    default_tenant_id: str = "default"

    jwt_secret: str = "dev-secret-change-me-0123456789abcdef0123456789abcdef"
    jwt_ttl_seconds: int = 86400
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "llm-security-scanner"

    cache_ttl_seconds: int = 300
    cache_max_size: int = 10000

    pdp_default_action: str = "allow"
    pdp_block_on_severity: list[str] = ["high", "critical"]

    audit_enabled: bool = True
    redact_audit_prompt: bool = True
    policy_version: str = "mvp-1.0"

    llm_provider_url: str = "http://localhost:8001"
    llm_timeout_seconds: int = 30
    llm_api_key: str | None = None

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/scanner"
    redis_url: str = "redis://localhost:6379/0"

    rate_limit_rps: int = 100

    rules_dir: str = "rules"

    log_level: str = "INFO"

    model_config = {"env_prefix": "SCANNER_"}


settings = Settings()
