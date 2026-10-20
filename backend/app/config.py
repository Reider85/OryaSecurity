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
    llm_api_path: str = "/v1/chat/completions"
    llm_model: str | None = "glm-5.3-flash"
    llm_timeout_seconds: int = 30
    llm_api_key: str | None = None

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/scanner"
    redis_url: str = "redis://localhost:6379/0"

    rate_limit_rps: int = 100

    rules_dir: str = "rules"

    streaming_chunk_size: int = 8
    toxicity_threshold: float = 0.7

    # ML toxicity model paths
    toxicity_model_path: str = "ml-models/toxicity/model_int8.onnx"
    toxicity_tokenizer_path: str = "ml-models/toxicity"
    toxicity_model_max_length: int = 64

    # Post-factum deep analysis (Prompt 6)
    post_factum_enabled: bool = True
    post_factum_timeout_seconds: float = 5.0
    post_factum_flag_severity: str = "high"

    # Embedding model (multilingual-e5, ONNX int8)
    embedding_model_path: str = "ml-models/embeddings/1/model_int8.onnx"
    embedding_tokenizer_path: str = "ml-models/embeddings/1"
    embedding_dim: int = 1024
    embedding_max_length: int = 512

    # Embedding cache (Redis)
    embedding_cache_ttl_seconds: int = 86400
    embedding_cache_max_entries: int = 1_000_000

    # Triton Inference Server (optional, overrides local ONNX)
    triton_url: str | None = None  # e.g. "localhost:8001" or "triton:8001"
    triton_model_name: str = "multilingual_e5"
    triton_timeout_ms: int = 2000

    # Qdrant vector search
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "prompt_attacks"
    qdrant_top_k: int = 10
    vector_match_threshold: float = 0.90

    # ML classifier (DeBERTa-v3-small, ONNX int8)
    classifier_model_path: str = "ml-models/classifier/v1/model_int8.onnx"
    classifier_tokenizer_path: str = "ml-models/classifier/v1"
    classifier_max_length: int = 512
    classifier_flag_threshold: float = 0.85

    # WebSocket notifications for SecOps UI
    ws_notifications_enabled: bool = True

    log_level: str = "INFO"

    model_config = {"env_prefix": "SCANNER_"}


settings = Settings()
