"""End-to-end: our OPAClient against a real OPA server with the repo's Rego
policies loaded (EP-12/Prompt 28). Complements the mock-based unit tests in
tests/core/test_opa_client.py."""

from __future__ import annotations

import pytest

from app.config import settings
from app.core.pdp.opa_client import OPAClient


@pytest.fixture
async def scanner_opa(opa_server: str) -> OPAClient:
    """OPAClient wired to the session OPA server (policies pre-loaded)."""
    client = OPAClient(opa_url=opa_server, bundle_service_url="", timeout_ms=5000)
    yield client
    await client.close()


class TestLiveEvaluation:
    @pytest.mark.asyncio
    async def test_prompt_injection_is_denied(self, scanner_opa: OPAClient):
        decision = await scanner_opa.evaluate(
            {
                "policy_version": "test",
                "detectors": {
                    "rules": {"matches": []},
                    "pii": {"found": []},
                    "ml_classifier": {
                        "top_class": "prompt_injection",
                        "confidence": 0.9,
                    },
                },
            }
        )
        assert decision.ok
        assert decision.allowed is False
        assert any("prompt injection" in m for m in decision.deny_reasons)

    @pytest.mark.asyncio
    async def test_benign_prompt_is_allowed(self, scanner_opa: OPAClient):
        decision = await scanner_opa.evaluate(
            {
                "policy_version": "test",
                "detectors": {
                    "rules": {"matches": []},
                    "pii": {"found": []},
                    "ml_classifier": {"top_class": "benign", "confidence": 0.95},
                },
            }
        )
        assert decision.ok
        assert decision.allowed is True
        assert decision.deny_reasons == []

    @pytest.mark.asyncio
    async def test_critical_rule_match_is_denied(self, scanner_opa: OPAClient):
        decision = await scanner_opa.evaluate(
            {
                "policy_version": "test",
                "detectors": {
                    "rules": {
                        "matches": [
                            {
                                "rule_id": "secret_aws_key",
                                "rule_name": "AWS Key",
                                "value_hash": "0" * 64,
                                "position": [0, 20],
                                "severity": "critical",
                                "action": "block",
                            }
                        ]
                    },
                    "pii": {"found": []},
                },
            }
        )
        assert decision.ok
        assert decision.allowed is False
        assert any("secret_aws_key" in m for m in decision.deny_reasons)

    @pytest.mark.asyncio
    async def test_high_severity_rule_is_denied(self, scanner_opa: OPAClient):
        decision = await scanner_opa.evaluate(
            {
                "policy_version": "test",
                "detectors": {
                    "rules": {
                        "matches": [
                            {
                                "rule_id": "pii_ssn",
                                "rule_name": "SSN",
                                "value_hash": "1" * 64,
                                "position": [0, 11],
                                "severity": "high",
                                "action": "log_only",
                            }
                        ]
                    },
                    "pii": {"found": [{"type": "pii_ssn", "rule_id": "pii_ssn"}]},
                },
            }
        )
        assert decision.ok
        assert decision.allowed is False

    @pytest.mark.asyncio
    async def test_version_fallback_without_bundle_service(
        self, scanner_opa: OPAClient
    ):
        assert await scanner_opa.get_active_policy_version() == settings.policy_version

    @pytest.mark.asyncio
    async def test_unreachable_opa_degrades_not_raises(self):
        client = OPAClient(
            opa_url="http://localhost:1",  # nothing listens here
            bundle_service_url="",
            timeout_ms=100,
        )
        try:
            decision = await client.evaluate({"detectors": {}})
            assert not decision.ok
            assert decision.error
            assert client.breaker_state in ("closed", "open", "half_open")
        finally:
            await client.close()
