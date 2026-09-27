from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.models.scan import ScanRequest, ScanResponse, RuleMatchResponse
from app.core.auth import verify_api_key, AuthInfo
from app.core.pdp import scan_text, decide
from app.core.cache import decision_cache
from app.core.audit import write_audit_event

router = APIRouter(tags=["scan"])


@router.post(
    "/scan",
    response_model=ScanResponse,
    summary="Scan a prompt for PII, secrets, and injection attempts",
)
async def scan_endpoint(
    body: ScanRequest,
    request: Request,
    auth_info: AuthInfo = Depends(verify_api_key),
) -> JSONResponse:
    request_id = str(uuid.uuid4())
    start = time.perf_counter()

    cached, cache_hit = decision_cache.get(body.prompt)

    if cache_hit and cached is not None:
        latency_ms = (time.perf_counter() - start) * 1000
        response = ScanResponse(
            verdict=cached["verdict"],
            reason=cached["reason"],
            latency_ms=round(latency_ms, 3),
            request_id=request_id,
            rules_matched=[
                RuleMatchResponse(**rm) for rm in cached["rules_matched"]
            ],
            cache_hit=True,
        )
        json_resp = JSONResponse(content=response.model_dump())
        json_resp.headers["X-Scanner-Verdict"] = cached["verdict"]
        json_resp.headers["X-Scanner-Cache"] = "HIT"
        json_resp.headers["X-Scanner-Request-Id"] = request_id
        return json_resp

    matches, prompt_hash = scan_text(body.prompt)
    verdict = decide(matches)
    latency_ms = (time.perf_counter() - start) * 1000

    rules_matched_dicts = [m.to_dict() for m in matches]

    decision_cache.set(
        prompt=body.prompt,
        verdict=verdict.action,
        reason=verdict.reason,
        rules_matched=rules_matched_dicts,
    )

    write_audit_event(
        request_id=request_id,
        tenant_id=auth_info.tenant_id or body.tenant_id,
        prompt_hash=prompt_hash,
        verdict=verdict.action,
        reason=verdict.reason,
        rules_matched=rules_matched_dicts,
        latency_ms=latency_ms,
        metadata=body.metadata,
    )

    response = ScanResponse(
        verdict=verdict.action,
        reason=verdict.reason,
        latency_ms=round(latency_ms, 3),
        request_id=request_id,
        rules_matched=[
            RuleMatchResponse(**rm) for rm in rules_matched_dicts
        ],
        cache_hit=False,
    )

    json_resp = JSONResponse(content=response.model_dump())
    json_resp.headers["X-Scanner-Verdict"] = verdict.action
    json_resp.headers["X-Scanner-Cache"] = "MISS"
    json_resp.headers["X-Scanner-Request-Id"] = request_id
    return json_resp
