"""Tests for the async PDP decision merger (EP-12/Prompt 28).

Covers: OPA deny wins, local block rules stay defense-in-depth, fail-open
degrade to the local rules engine, fail-closed BLOCK, policy_version
propagation into the result (-> audit log).
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.core.pdp import decide_with_opa, build_opa_input
from app.core.pdp.decision import PDPResult, get_active_policy_version
from app.core.pdp.opa_client import OPADecision, set_opa_client
from app.core.rules.base import RuleMatch


def _match(
    rule_id: str = "pii_ssn",
    severity: str = "low",
    action: str = "log_only",
) -> RuleMatch:
    return RuleMatch(
        rule_id=rule_id,
        rule_name=rule_id,
        value_hash="a" * 64,
        position=(0, 5),
        severity=severity,
        action=action,
    )


class _FakeOPAClient:
    def __init__(
        self,
        decision: OPADecision | None = None,
        version: str = "9.9.9",
        error: bool = False,
    ):
        self._decision = decision or OPADecision(allowed=True, deny_reasons=[])
        self._version = version
        self._error = error
        self.breaker_state = "closed"
        self.evaluations: list[dict] = []

    async def get_active_policy_version(self) -> str:
        return self._version

    async def evaluate(self, input_doc: dict) -> OPADecision:
        self.evaluations.append(input_doc)
        if self._error:
            return OPADecision(error="opa_unavailable: test")
        return self._decision


@pytest.fixture(autouse=True)
def _reset_client():
    set_opa_client(None)
    yield
    set_opa_client(None)


class TestDecideWithOpa:
    @pytest.mark.asyncio
    async def test_opa_disabled_uses_local_engine(self):
        result = await decide_with_opa([_match(action="block")])
        assert isinstance(result, PDPResult)
        assert result.source == "local"
        assert result.policy_version == settings.policy_version
        assert result.verdict.is_block

    @pytest.mark.asyncio
    async def test_opa_deny_wins(self):
        set_opa_client(
            _FakeOPAClient(
                decision=OPADecision(
                    allowed=False,
                    deny_reasons=["Blocked: prompt injection (confidence 0.90)"],
                )
            )
        )
        result = await decide_with_opa([], request_id="req-1", tenant_id="t1")

        assert result.source == "opa"
        assert result.verdict.is_block
        assert "Blocked by policy" in result.verdict.reason
        assert "prompt injection" in result.verdict.reason
        assert result.policy_version == "9.9.9"
        assert result.opa_deny_reasons

    @pytest.mark.asyncio
    async def test_opa_allow_but_local_block_still_blocks(self):
        # defense in depth: OPA says allow, local critical rule says block
        set_opa_client(_FakeOPAClient(decision=OPADecision(allowed=True, deny_reasons=[])))
        result = await decide_with_opa([_match(rule_id="secret_aws_key", action="block")])

        assert result.source == "opa"
        assert result.verdict.is_block
        assert "Blocked by rules" in result.verdict.reason

    @pytest.mark.asyncio
    async def test_opa_allow_and_local_allow(self):
        set_opa_client(_FakeOPAClient(decision=OPADecision(allowed=True, deny_reasons=[])))
        result = await decide_with_opa([_match(action="log_only")])

        assert result.source == "opa"
        assert result.verdict.action == "allow"
        assert result.policy_version == "9.9.9"

    @pytest.mark.asyncio
    async def test_opa_error_fails_open_to_local(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "pdp_opa_fail_open", True)
        set_opa_client(_FakeOPAClient(error=True))
        result = await decide_with_opa([_match(action="block")])

        assert result.source == "local_fallback"
        assert result.verdict.is_block  # local block rules still apply
        assert result.policy_version == "9.9.9"  # version still from manifest

    @pytest.mark.asyncio
    async def test_opa_error_fails_closed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "pdp_opa_fail_open", False)
        set_opa_client(_FakeOPAClient(error=True))
        result = await decide_with_opa([_match(action="log_only")])

        assert result.source == "local_fallback"
        assert result.verdict.is_block
        assert "fail-closed" in result.verdict.reason

    @pytest.mark.asyncio
    async def test_evaluated_input_document(self):
        client = _FakeOPAClient()
        set_opa_client(client)
        pii = _match(rule_id="pii_ssn", severity="high")
        other = _match(rule_id="secret_aws_key", action="block")

        await decide_with_opa(
            [pii, other], request_id="req-42", tenant_id="acme"
        )

        input_doc = client.evaluations[0]
        assert input_doc["policy_version"] == "9.9.9"
        assert input_doc["request_id"] == "req-42"
        assert input_doc["tenant_id"] == "acme"
        assert len(input_doc["detectors"]["rules"]["matches"]) == 2
        assert input_doc["detectors"]["pii"]["found"] == [
            {"type": "pii_ssn", "rule_id": "pii_ssn", "severity": "high"}
        ]
        assert "ml_classifier" not in input_doc["detectors"]

    @pytest.mark.asyncio
    async def test_ml_context_included_when_provided(self):
        client = _FakeOPAClient()
        set_opa_client(client)
        await decide_with_opa([], ml={"top_class": "benign", "confidence": 0.95})
        assert client.evaluations[0]["detectors"]["ml_classifier"] == {
            "top_class": "benign",
            "confidence": 0.95,
        }


class TestBuildOpaInput:
    def test_schema_matches_rego_expectations(self):
        doc = build_opa_input(
            [_match()],
            policy_version="1.0.0",
            tenant_id="t",
            request_id="r",
        )
        assert set(doc) == {"policy_version", "tenant_id", "request_id", "detectors"}
        assert set(doc["detectors"]) == {"rules", "pii"}
        assert doc["detectors"]["rules"]["matches"][0]["rule_id"] == "pii_ssn"
        assert doc["detectors"]["pii"]["found"]

    def test_empty_matches(self):
        doc = build_opa_input([], policy_version="1.0.0")
        assert doc["detectors"]["rules"]["matches"] == []
        assert doc["detectors"]["pii"]["found"] == []
        assert doc["tenant_id"] == settings.default_tenant_id


class TestGetActivePolicyVersionHelper:
    @pytest.mark.asyncio
    async def test_without_client_returns_setting(self):
        assert await get_active_policy_version() == settings.policy_version

    @pytest.mark.asyncio
    async def test_with_client_returns_manifest_version(self):
        set_opa_client(_FakeOPAClient(version="7.7.7"))
        assert await get_active_policy_version() == "7.7.7"


class TestMetricsRecorded:
    @pytest.mark.asyncio
    async def test_decisions_counter_increments(self):
        from app.core.metrics import scanner_pdp_decisions_total

        before = scanner_pdp_decisions_total.labels(
            source="local", action="allow"
        )._value.get()
        await decide_with_opa([])
        after = scanner_pdp_decisions_total.labels(
            source="local", action="allow"
        )._value.get()
        assert after > before
