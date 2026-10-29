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

    # Per-tenant fallback policy for the ML classifier circuit breaker
    # (EP-14 / Prompt 37, ADR-0008): "default" -> degrade (rule-only),
    # "strict" -> fail-closed (BLOCK + audit "ml_unavailable").
    # Env: SCANNER_TENANT_POLICIES='{"acme": "strict"}' (JSON).
    tenant_policies: dict[str, str] = {}

    audit_enabled: bool = True
    redact_audit_prompt: bool = True
    policy_version: str = "mvp-1.0"

    # OPA Policy Decision Point (EP-12/Prompt 25 + 28)
    opa_url: str = ""  # e.g. "http://localhost:8181"; empty = OPA integration off
    bundle_service_url: str = ""  # e.g. "http://localhost:8081" (serves /bundles/manifest.json)
    pdp_opa_enabled: bool = False
    pdp_opa_fail_open: bool = True  # OPA unreachable -> local rules engine (degrade)
    opa_timeout_ms: int = 200
    opa_policy_package: str = "tenant_default"  # Rego package for allow/deny evaluation
    policy_version_cache_ttl_seconds: int = 30  # how long active version is cached in-process

    # HashiCorp Vault PII redaction (EP-13/Prompt 31, ADR-0007)
    vault_url: str = ""  # e.g. "http://localhost:8200"; empty = Vault integration off
    vault_token: str | None = None  # root/app token (from backend/scripts/vault_init.sh)
    vault_namespace: str | None = None  # Enterprise namespaces (ADR-0011, BETA)
    vault_timeout_ms: int = 200
    vault_secret_mount: str = "tokenization"  # mount path of the secrets engine
    vault_key_name: str = "default"  # key within the engine (tenant key in BETA)
    vault_engine: str = "auto"  # auto | tokenization | transit (OSS fallback, convergent)
    vault_breaker_fail_max: int = 5
    vault_breaker_reset_timeout: float = 30.0  # seconds
    # Token TTL / auto-cleanup (EP-13/Prompt 34): the cleanup job rotates the
    # encode key every ``vault_token_ttl_seconds`` and keeps the previous key
    # version decryptable, so minted PII tokens expire after (ttl, 2*ttl].
    vault_token_ttl_seconds: int = 300
    vault_token_ttl_grace_versions: int = 1  # key versions kept decryptable after a rotation

    # Presidio NER for non-standard PII (EP-13/Prompt 35, ADR-0007)
    # Requires the optional ``ner`` extra: pip install -e ".[ner]".
    # When disabled (ALPHA default) detection stays regex-only.
    presidio_enabled: bool = False
    presidio_language: str = "ru"
    presidio_min_score: float = 0.4  # NER hits below this confidence are dropped

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
    qdrant_ef_search: int = 64
    vector_match_threshold: float = 0.90

    # Qdrant seed collection (Prompt 12)
    seed_target_count: int = 10_000
    seed_batch_size: int = 256
    advbench_dataset_path: str = "backend/app/ml/datasets/advbench.json"
    jailbreakbench_dataset_path: str = "backend/app/ml/datasets/jailbreakbench.json"

    # ML classifier (DeBERTa-v3-small, ONNX int8)
    classifier_model_path: str = "ml-models/classifier/v1/model_int8.onnx"
    classifier_tokenizer_path: str = "ml-models/classifier/v1"
    classifier_max_length: int = 512
    classifier_flag_threshold: float = 0.85

    # SHAP explainability for classifier decisions (Prompt 19)
    explainability_enabled: bool = True
    explainability_top_k: int = 5
    explainability_timeout_seconds: float = 10.0
    explainability_max_length: int = 128
    explainability_cache_max_entries: int = 512

    # WebSocket notifications for SecOps UI
    ws_notifications_enabled: bool = True

    log_level: str = "INFO"

    # OpenTelemetry tracing (Prompt 40)
    otel_tracing_enabled: bool = True
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_version: str = "0.2.0"

    model_config = {"env_prefix": "SCANNER_"}


settings = Settings()
