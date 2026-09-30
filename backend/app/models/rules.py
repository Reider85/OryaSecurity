from typing import Optional
from pydantic import BaseModel, Field


class RuleResponse(BaseModel):
    """Response model for a single security rule."""
    id: str
    name: str
    type: str
    pattern: str
    severity: str
    action: str
    version: str
    description: Optional[str] = None
    enabled: bool = True
    source_file: str


class RuleListResponse(BaseModel):
    """Response model for the list of rules."""
    items: list[RuleResponse]
    total: int
    last_loaded_time: Optional[float] = None


class RuleFields(BaseModel):
    """Rule payload accepted by create/save endpoints."""
    id: str
    name: str
    type: str
    pattern: str
    severity: str
    action: str
    version: str
    description: Optional[str] = None
    enabled: bool = True


class RuleCreateRequest(BaseModel):
    """Request model for creating a new rule."""
    source_file: str = Field(..., description="Target .yaml file name inside the rules directory")
    rule: RuleFields


class RuleTestRequest(BaseModel):
    """Request model for testing text against rules."""
    text: str = Field(..., min_length=1, description="Text to scan against rules")
    rule_id: Optional[str] = Field(None, description="Optional rule id filter")


class RuleMatchResponse(BaseModel):
    """Response model for a rule match (value is hashed for privacy)."""
    rule_id: str
    rule_name: str
    value_hash: str
    position: list[int]
    severity: str
    action: str


class ReloadResponse(BaseModel):
    """Response model for a rules reload."""
    status: str
    total_rules: int
    enabled_rules: int
