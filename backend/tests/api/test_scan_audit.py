from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.api import scan as scan_api
from app.core.auth import AuthInfo, verify_api_key
from app.main import app


class FakeCache:
    """Decision cache double: always a miss unless configured otherwise."""

    def __init__(self, hit: bool = False) -> None:
        self.hit = hit
        self.set_calls: list[dict] = []

    async def get(self, prompt: str):
        if self.hit:
            return (
                {
                    "verdict": "allow",
                    "reason": "cached",
                    "rules_matched": [],
                    "ts": 0.0,
                },
                True,
            )
        return None, False

    async def set(self, prompt: str, verdict: str, reason: str, rules_matched: list[dict]) -> None:
        self.set_calls.append(
            {"prompt": prompt, "verdict": verdict, "reason": reason, "rules_matched": rules_matched}
        )


class RecordingAudit:
    def __init__(self, exc: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._exc = exc

    async def write_event(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc


@pytest.fixture
def auth_override():
    app.dependency_overrides[verify_api_key] = lambda: AuthInfo(
        api_key="test", tenant_id="tenant-a"
    )
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def test_client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


class TestScanAuditWiring:
    def test_write_event_called_with_scan_result(
        self, test_client: TestClient, auth_override, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audit = RecordingAudit()
        monkeypatch.setattr(scan_api, "decision_cache", FakeCache())
        monkeypatch.setattr(scan_api, "write_event", audit.write_event)

        prompt = "Hello, how are you?"
        resp = test_client.post("/scan", json={"prompt": prompt})

        assert resp.status_code == 200
        assert len(audit.calls) == 1
        call = audit.calls[0]
        assert call["prompt_hash"] == hashlib.sha256(prompt.encode()).hexdigest()
        assert call["verdict"] == resp.json()["verdict"]
        assert call["tenant_id"] == "tenant-a"
        assert call["request_id"] == resp.json()["request_id"]
        assert call["latency_ms"] >= 0

    def test_write_event_receives_metadata(
        self, test_client: TestClient, auth_override, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audit = RecordingAudit()
        monkeypatch.setattr(scan_api, "decision_cache", FakeCache())
        monkeypatch.setattr(scan_api, "write_event", audit.write_event)

        resp = test_client.post(
            "/scan",
            json={"prompt": "hi", "metadata": {"model": "gpt-test"}},
        )

        assert resp.status_code == 200
        assert audit.calls[0]["metadata"] == {"model": "gpt-test"}

    def test_audit_failure_does_not_break_response(
        self, test_client: TestClient, auth_override, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audit = RecordingAudit(exc=RuntimeError("db down"))
        monkeypatch.setattr(scan_api, "decision_cache", FakeCache())
        monkeypatch.setattr(scan_api, "write_event", audit.write_event)

        resp = test_client.post("/scan", json={"prompt": "hi"})

        assert resp.status_code == 200
        assert resp.json()["verdict"] == "allow"
        assert resp.headers["X-Scanner-Verdict"] == "allow"
        assert len(audit.calls) == 1

    def test_rules_matched_forwarded_for_blocked_prompt(
        self, test_client: TestClient, auth_override, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audit = RecordingAudit()
        monkeypatch.setattr(scan_api, "decision_cache", FakeCache())
        monkeypatch.setattr(scan_api, "write_event", audit.write_event)

        resp = test_client.post("/scan", json={"prompt": "My SSN is 123-45-6789"})

        assert resp.status_code == 200
        rules = audit.calls[0]["rules_matched"]
        assert rules
        assert all("rule_id" in rule and "position" in rule for rule in rules)

    def test_cache_hit_skips_audit(
        self, test_client: TestClient, auth_override, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audit = RecordingAudit()
        monkeypatch.setattr(scan_api, "decision_cache", FakeCache(hit=True))
        monkeypatch.setattr(scan_api, "write_event", audit.write_event)

        resp = test_client.post("/scan", json={"prompt": "hi"})

        assert resp.status_code == 200
        assert resp.json()["cache_hit"] is True
        assert audit.calls == []
