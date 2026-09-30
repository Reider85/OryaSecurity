from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import verify_api_key
from app.core.pdp import rule_set
from app.core.rules.loader import (
    RuleExistsError,
    RuleNotFoundError,
    RuleValidationError,
)
from app.models.rules import (
    ReloadResponse,
    RuleCreateRequest,
    RuleFields,
    RuleListResponse,
    RuleMatchResponse,
    RuleResponse,
    RuleTestRequest,
)

router = APIRouter(prefix="/api/v1", tags=["rules"])


def _rule_to_response(rule, source_file: str) -> RuleResponse:
    data = rule_set.rule_to_dict(rule)
    return RuleResponse(**data, source_file=source_file)


def _source_file_for(rule) -> str:
    source = rule_set.find_rule_source(rule.id)
    return source.name if source else "unknown"


@router.get("/rules", response_model=RuleListResponse)
async def list_rules(api_key: str = Depends(verify_api_key)) -> RuleListResponse:
    """List all loaded rules with their source YAML files."""
    items = [
        _rule_to_response(rule, _source_file_for(rule))
        for rule in rule_set.rules
    ]
    return RuleListResponse(
        items=items,
        total=len(items),
        last_loaded_time=rule_set.last_loaded_time,
    )


@router.get("/rules/{rule_id}", response_model=RuleResponse)
async def get_rule(rule_id: str, api_key: str = Depends(verify_api_key)) -> RuleResponse:
    """Get a single rule by id."""
    rule = rule_set.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return _rule_to_response(rule, _source_file_for(rule))


@router.post("/rules", response_model=RuleResponse, status_code=201)
async def create_rule(
    payload: RuleCreateRequest,
    api_key: str = Depends(verify_api_key),
) -> RuleResponse:
    """Create a new rule in the given YAML file."""
    rule_data: Dict[str, Any] = payload.rule.model_dump()
    try:
        created = rule_set.create_rule(rule_data, payload.source_file)
    except RuleExistsError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except RuleValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _rule_to_response(created, payload.source_file)


@router.post("/rules/reload", response_model=ReloadResponse)
async def reload_rules(api_key: str = Depends(verify_api_key)) -> ReloadResponse:
    """Reload all rules from disk without restarting the service."""
    await rule_set.reload_rules()
    stats = rule_set.get_stats()
    return ReloadResponse(
        status="ok",
        total_rules=stats["total_rules"],
        enabled_rules=stats["enabled_rules"],
    )


@router.post("/rules/test", response_model=list[RuleMatchResponse])
async def test_rules(
    payload: RuleTestRequest,
    api_key: str = Depends(verify_api_key),
) -> list[RuleMatchResponse]:
    """Run text against the current rule set and return matches."""
    if not payload.text.strip():
        raise HTTPException(status_code=422, detail="text must not be empty")

    matches = rule_set.match(payload.text)
    if payload.rule_id:
        matches = [m for m in matches if m.rule_id == payload.rule_id]

    return [RuleMatchResponse(**m.to_dict()) for m in matches]


@router.post("/rules/{rule_id}", response_model=RuleResponse)
async def save_rule(
    rule_id: str,
    rule_fields: RuleFields,
    api_key: str = Depends(verify_api_key),
) -> RuleResponse:
    """Update an existing rule and persist it to its YAML file."""
    rule_data: Dict[str, Any] = rule_fields.model_dump()
    # The path id is authoritative for lookup; allow the body id to rename the rule.
    if rule_data.get("id") != rule_id and rule_set.get_rule(rule_id) is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")

    try:
        saved = rule_set.save_rule(rule_id, rule_data)
    except RuleNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RuleValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return _rule_to_response(saved, _source_file_for(saved))
