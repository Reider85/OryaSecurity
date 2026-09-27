from pydantic import BaseModel, Field


class ScanRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=100_000, description="Text to scan")
    tenant_id: str | None = Field(None, max_length=64)
    metadata: dict | None = None


class RuleMatchResponse(BaseModel):
    rule_id: str
    rule_name: str
    value_hash: str
    position: list[int]
    severity: str
    action: str


class ScanResponse(BaseModel):
    verdict: str
    reason: str
    latency_ms: float
    request_id: str
    rules_matched: list[RuleMatchResponse] = []
    cache_hit: bool = False
