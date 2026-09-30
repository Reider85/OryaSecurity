from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.core.auth import AuthInfo, verify_api_key
from app.core.pdp import rule_set


@pytest.fixture
def auth_override():
    """Override API-key auth so rules API tests do not require Postgres."""
    def _override() -> AuthInfo:
        return AuthInfo(api_key="test-key", tenant_id="test-tenant")

    app.dependency_overrides[verify_api_key] = _override
    yield
    app.dependency_overrides.pop(verify_api_key, None)


@pytest.fixture
def unauthorized_override():
    def _override() -> AuthInfo:
        raise HTTPException(status_code=401, detail="Invalid API key")

    app.dependency_overrides[verify_api_key] = _override
    yield
    app.dependency_overrides.pop(verify_api_key, None)


@pytest.fixture
def temp_rules_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the global RuleSet at a temp directory with sample YAML rules."""
    rules_src = Path(__file__).resolve().parents[2] / "rules"
    for yaml_file in rules_src.glob("*.yaml"):
        shutil.copy(yaml_file, tmp_path / yaml_file.name)

    monkeypatch.setattr(rule_set, "rules_dir", tmp_path)
    rule_set._load_rules()
    yield tmp_path
    rule_set._load_rules()  # restore original rules


class TestListRules:
    def test_list_rules_returns_all_rules(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.get("/api/v1/rules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == len(data["items"]) > 0
        ids = {item["id"] for item in data["items"]}
        assert "pii_ssn_us" in ids
        assert "secret_aws_key" in ids

    def test_list_rules_includes_source_file(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.get("/api/v1/rules")
        items = {item["id"]: item for item in resp.json()["items"]}
        assert items["pii_ssn_us"]["source_file"] == "pii.yaml"
        assert items["secret_aws_key"]["source_file"] == "secrets.yaml"

    def test_list_rules_unauthorized(self, client: TestClient, unauthorized_override):
        resp = client.get("/api/v1/rules")
        assert resp.status_code == 401


class TestGetRule:
    def test_get_rule_by_id(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.get("/api/v1/rules/pii_ssn_us")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "pii_ssn_us"
        assert data["severity"] == "high"
        assert data["action"] == "block"
        assert data["source_file"] == "pii.yaml"
        assert data["pattern"] == r"\b\d{3}-\d{2}-\d{4}\b"

    def test_get_rule_not_found(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.get("/api/v1/rules/nonexistent_rule")
        assert resp.status_code == 404


class TestSaveRule:
    def test_save_rule_updates_yaml_file(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules/pii_email",
            json={
                "id": "pii_email",
                "name": "Email Address (updated)",
                "type": "regex",
                "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
                "severity": "high",
                "action": "block",
                "version": "1.1.0",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Email Address (updated)"
        assert data["severity"] == "high"
        assert data["version"] == "1.1.0"

        # Verify the file on disk was updated
        pii_file = temp_rules_dir / "pii.yaml"
        with open(pii_file, encoding="utf-8") as f:
            rules_data = yaml.safe_load(f)
        entry = next(r for r in rules_data["rules"] if r["id"] == "pii_email")
        assert entry["name"] == "Email Address (updated)"
        assert entry["severity"] == "high"

        # Verify the in-memory rule set was reloaded
        assert rule_set.get_rule("pii_email").severity == "high"

    def test_save_rule_invalid_severity(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules/pii_email",
            json={
                "id": "pii_email",
                "name": "Email",
                "type": "regex",
                "pattern": r"\btest@x\.com\b",
                "severity": "ultra",
                "action": "block",
                "version": "1.0.0",
            },
        )
        assert resp.status_code == 422
        # Original rule unchanged
        assert rule_set.get_rule("pii_email").severity == "medium"

    def test_save_rule_invalid_regex(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules/pii_email",
            json={
                "id": "pii_email",
                "name": "Email",
                "type": "regex",
                "pattern": "([unclosed",
                "severity": "medium",
                "action": "block",
                "version": "1.0.0",
            },
        )
        assert resp.status_code == 422

    def test_save_rule_not_found(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules/missing_rule",
            json={
                "id": "missing_rule",
                "name": "Missing",
                "type": "regex",
                "pattern": r"\bfoo\b",
                "severity": "low",
                "action": "log_only",
                "version": "1.0.0",
            },
        )
        assert resp.status_code == 404

    def test_save_rule_renames_id(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules/pii_phone",
            json={
                "id": "pii_phone_v2",
                "name": "Phone Number",
                "type": "regex",
                "pattern": r"\b\+?[1-9]\d{1,14}\b",
                "severity": "low",
                "action": "log_only",
                "version": "1.0.0",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == "pii_phone_v2"
        assert rule_set.get_rule("pii_phone_v2") is not None
        assert rule_set.get_rule("pii_phone") is None


class TestCreateRule:
    def test_create_rule_success(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules",
            json={
                "source_file": "pii.yaml",
                "rule": {
                    "id": "pii_api_key_ru",
                    "name": "RU API Key",
                    "type": "regex",
                    "pattern": r"\bru_key_[0-9a-f]{16}\b",
                    "severity": "high",
                    "action": "block",
                    "version": "1.0.0",
                },
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "pii_api_key_ru"
        assert data["source_file"] == "pii.yaml"
        assert rule_set.get_rule("pii_api_key_ru") is not None

        with open(temp_rules_dir / "pii.yaml", encoding="utf-8") as f:
            rules_data = yaml.safe_load(f)
        assert any(r["id"] == "pii_api_key_ru" for r in rules_data["rules"])

    def test_create_rule_new_file(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules",
            json={
                "source_file": "custom.yaml",
                "rule": {
                    "id": "custom_rule_one",
                    "name": "Custom Rule",
                    "type": "regex",
                    "pattern": r"\bCUSTOM-\d{4}\b",
                    "severity": "medium",
                    "action": "log_only",
                    "version": "1.0.0",
                },
            },
        )
        assert resp.status_code == 201
        assert (temp_rules_dir / "custom.yaml").exists()

    def test_create_rule_duplicate_id(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules",
            json={
                "source_file": "pii.yaml",
                "rule": {
                    "id": "pii_ssn_us",
                    "name": "Duplicate SSN",
                    "type": "regex",
                    "pattern": r"\b\d{3}-\d{2}-\d{4}\b",
                    "severity": "high",
                    "action": "block",
                    "version": "1.0.0",
                },
            },
        )
        assert resp.status_code == 409

    def test_create_rule_invalid_schema(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules",
            json={
                "source_file": "pii.yaml",
                "rule": {
                    "id": "bad_rule",
                    "name": "Bad",
                    "type": "regex",
                    "pattern": r"\bok\b",
                    "severity": "invalid",
                    "action": "block",
                    "version": "1.0.0",
                },
            },
        )
        assert resp.status_code == 422

    def test_create_rule_rejects_path_traversal(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules",
            json={
                "source_file": "../evil.yaml",
                "rule": {
                    "id": "evil_rule",
                    "name": "Evil",
                    "type": "regex",
                    "pattern": r"\bx\b",
                    "severity": "low",
                    "action": "allow",
                    "version": "1.0.0",
                },
            },
        )
        assert resp.status_code == 422


class TestReloadRules:
    def test_reload_rules(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post("/api/v1/rules/reload")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["total_rules"] > 0
        assert data["enabled_rules"] > 0


class TestTestEndpoint:
    def test_test_endpoint_detects_ssn(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules/test",
            json={"text": "My SSN is 123-45-6789"},
        )
        assert resp.status_code == 200
        matches = resp.json()
        assert any(m["rule_id"] == "pii_ssn_us" for m in matches)

    def test_test_endpoint_returns_hash_not_value(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules/test",
            json={"text": "My SSN is 123-45-6789"},
        )
        matches = resp.json()
        ssn_match = next(m for m in matches if m["rule_id"] == "pii_ssn_us")
        assert "123-45-6789" not in ssn_match["value_hash"]
        assert len(ssn_match["value_hash"]) == 16
        assert ssn_match["position"] == [10, 21]
        assert ssn_match["severity"] == "high"
        assert ssn_match["action"] == "block"

    def test_test_endpoint_rule_id_filter(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules/test",
            json={"text": "My SSN is 123-45-6789 and email a@b.com", "rule_id": "pii_ssn_us"},
        )
        matches = resp.json()
        assert len(matches) == 1
        assert matches[0]["rule_id"] == "pii_ssn_us"

    def test_test_endpoint_no_matches(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules/test",
            json={"text": "Just a clean hello world message"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_test_endpoint_empty_text(self, client: TestClient, auth_override, temp_rules_dir):
        resp = client.post(
            "/api/v1/rules/test",
            json={"text": "   "},
        )
        assert resp.status_code == 422


class TestEndToEndEditorFlow:
    def test_edit_save_then_scan_reflects_change(
        self, client: TestClient, auth_override, temp_rules_dir
    ):
        """Prompt 23 AC: edit rule → save → verify it applied."""
        # Save rule with tighter pattern (only .ru emails)
        resp = client.post(
            "/api/v1/rules/pii_email",
            json={
                "id": "pii_email",
                "name": "Email Address",
                "type": "regex",
                "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.ru\b",
                "severity": "high",
                "action": "block",
                "version": "2.0.0",
            },
        )
        assert resp.status_code == 200

        # .com email no longer matches this rule
        resp = client.post(
            "/api/v1/rules/test",
            json={"text": "Contact me at john@example.com", "rule_id": "pii_email"},
        )
        assert resp.json() == []

        # .ru email matches
        resp = client.post(
            "/api/v1/rules/test",
            json={"text": "Contact me at ivan@example.ru", "rule_id": "pii_email"},
        )
        matches = resp.json()
        assert len(matches) == 1
        assert matches[0]["rule_id"] == "pii_email"
