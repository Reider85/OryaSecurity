from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.scan import router as scan_router
from app.api.health import router as health_router
from app.api.metrics import router as metrics_router
from app.api.openai_compat import router as openai_compat_router
from app.api.v2 import v2_router as v2_router
from app.api.admin_apikey import router as admin_apikey_router
from app.api.v1 import (
    metrics_router as v1_metrics_router,
    audit_router as v1_audit_router,
    rules_router as v1_rules_router,
    cache_router as v1_cache_router,
    auth_router as v1_auth_router,
    decisions_router as v1_decisions_router,
    charts_router as v1_charts_router,
)
from app.core.headers import ScannerHeadersMiddleware
from app.db.session import init_db, close_db
from app.db.redis_client import init_redis, close_redis
from app.db.qdrant_client import init_qdrant, close_qdrant
from app.core.bootstrap import ensure_bootstrap_api_keys
from app.core.metrics import scanner_uptime_seconds, python_info
from app.core.detectors import get_shared_toxicity_detector
from app.core.pdp.opa_client import init_opa_client, close_opa_client
import time
import sys
import platform

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)
logger = structlog.get_logger()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added after CORS so that it sits *inside* the CORS layer: Starlette runs the
# last-registered middleware first, and CORS needs to be able to decorate error
# responses produced further down the stack.
app.add_middleware(ScannerHeadersMiddleware)


@app.on_event("startup")
async def startup_event():
    """Initialize database, Redis, Qdrant, ensure bootstrap API keys, and ML models on startup."""
    await init_db()
    await init_redis()
    await init_qdrant()  # Non-fatal initialization (graceful degradation if Qdrant unavailable)
    await ensure_bootstrap_api_keys()
    
    # Initialize toxicity model (with fallback if files missing)
    await init_toxicity_model()

    # Initialize post-factum deep-analysis stack (Prompt 6)
    init_post_factum_stack()

    # Initialize OPA PDP client (EP-12/Prompt 28) — no-op when disabled
    init_opa_client()
    
    # Initialize uptime and Python info metrics
    scanner_uptime_seconds.set(time.time())
    python_info.labels(
        version=sys.version.split()[0],
        implementation=platform.python_implementation(),
        platform=platform.platform(),
    ).set(1)


@app.on_event("shutdown")
async def shutdown_event():
    """Close Qdrant, Redis, and database connections on shutdown."""
    await close_opa_client()
    await close_qdrant()
    await close_redis()
    await close_db()


app.include_router(health_router)
app.include_router(metrics_router)
app.include_router(scan_router)
app.include_router(openai_compat_router)
app.include_router(v2_router)
app.include_router(admin_apikey_router)
app.include_router(v1_metrics_router)
app.include_router(v1_audit_router)
app.include_router(v1_rules_router)
app.include_router(v1_cache_router)
app.include_router(v1_auth_router)
app.include_router(v1_decisions_router)
app.include_router(v1_charts_router)


async def init_toxicity_model():
    """Initialize the toxicity model with graceful fallback."""
    try:
        from app.ml.models.toxicity_model import load_toxicity_model
        
        # Try to load the ML model
        load_toxicity_model(
            model_path=settings.toxicity_model_path,
            tokenizer_path=settings.toxicity_tokenizer_path,
            max_length=settings.toxicity_model_max_length,
        )
        
        # Verify model loaded successfully
        detector = get_shared_toxicity_detector()
        if detector is None:
            raise RuntimeError("Model loaded but detector not created")
            
        logger.info("toxicity_model_loaded", model_path=settings.toxicity_model_path)
        
    except FileNotFoundError:
        logger.warning(
            "toxicity_model_missing",
            model_path=settings.toxicity_model_path,
            tokenizer_path=settings.toxicity_tokenizer_path,
            fallback="heuristic"
        )
    except ImportError as e:
        logger.warning(
            "toxicity_model_deps_missing",
            error=str(e),
            fallback="heuristic"
        )
    except Exception as e:
        logger.error(
            "toxicity_model_load_failed",
            error=str(e),
            fallback="heuristic"
        )


def init_post_factum_stack():
    """Initialize embedding, vector search, ML classifier + PostFactumAnalyzer.

    Every component degrades gracefully when model files or Qdrant are
    unavailable: the corresponding analysis step is skipped at runtime.
    """
    from app.core.detectors.embedding import (
        EmbeddingClient,
        set_shared_embedding_client,
    )
    from app.core.detectors.vector_search import (
        VectorSearcher,
        set_shared_vector_searcher,
    )
    from app.core.detectors.ml_classifier import (
        PromptClassifier,
        set_shared_classifier,
    )
    from app.core.detectors.toxicity import get_shared_toxicity_detector
    from app.core.streaming.post_factum import set_post_factum_analyzer, PostFactumAnalyzer
    from app.ml.triton_client import (
        TritonEmbeddingClient,
        set_shared_triton_embedding_client,
    )

    # Choose embedding client: Triton if configured, else local ONNX
    if settings.triton_url:
        embedding_client = TritonEmbeddingClient(
            triton_url=settings.triton_url,
            model_name=settings.triton_model_name,
            tokenizer_path=settings.embedding_tokenizer_path,
            embedding_dim=settings.embedding_dim,
            max_length=settings.embedding_max_length,
            timeout_ms=settings.triton_timeout_ms,
        )
        set_shared_triton_embedding_client(embedding_client if embedding_client.available else None)
        set_shared_embedding_client(embedding_client if embedding_client.available else None)
    else:
        embedding_client = EmbeddingClient(
            model_path=settings.embedding_model_path,
            tokenizer_path=settings.embedding_tokenizer_path,
            embedding_dim=settings.embedding_dim,
            max_length=settings.embedding_max_length,
        )
        set_shared_embedding_client(embedding_client if embedding_client.available else None)
        set_shared_triton_embedding_client(None)  # Explicitly disable Triton

    vector_searcher = VectorSearcher(
        url=settings.qdrant_url,
        collection=settings.qdrant_collection,
        top_k=settings.qdrant_top_k,
        ef_search=settings.qdrant_ef_search,
    )
    set_shared_vector_searcher(vector_searcher)

    classifier = PromptClassifier(
        model_path=settings.classifier_model_path,
        tokenizer_path=settings.classifier_tokenizer_path,
        max_length=settings.classifier_max_length,
        flag_threshold=settings.classifier_flag_threshold,
    )
    set_shared_classifier(classifier if classifier.available else None)

    set_post_factum_analyzer(
        PostFactumAnalyzer(
            embedding_client=embedding_client if embedding_client.available else None,
            vector_searcher=vector_searcher,
            classifier=classifier if classifier.available else None,
            toxicity_detector=get_shared_toxicity_detector(),
        )
    )
    logger.info(
        "post_factum_stack_initialized",
        embedding_available=embedding_client.available,
        classifier_available=classifier.available,
        qdrant_url=settings.qdrant_url,
    )