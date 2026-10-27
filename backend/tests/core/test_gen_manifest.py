"""Tests for policies/scripts/gen_opa_manifest.py (EP-12/Prompt 28)."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent
SCRIPT = REPO_ROOT / "policies" / "scripts" / "gen_opa_manifest.py"
POLICIES_DIR = REPO_ROOT / "policies"


def _load_module():
    spec = importlib.util.spec_from_file_location("gen_opa_manifest", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gen = _load_module()


class TestBuildManifest:
    def test_real_policies_dir(self):
        manifest = gen.build_manifest(
            POLICIES_DIR,
            version="1.0.0",
            git_commit="abc123",
            created_at=datetime(2026, 9, 27, 10, 0, 0, tzinfo=timezone.utc),
        )

        assert manifest["version"] == "1.0.0"
        assert manifest["git_commit"] == "abc123"
        assert manifest["created_at"] == "2026-09-27T10:00:00Z"

        ids = [b["id"] for b in manifest["bundles"]]
        assert ids == ["global", "tenants_default"]
        paths = {b["id"]: b["path"] for b in manifest["bundles"]}
        assert paths["global"] == "global/"
        assert paths["tenants_default"] == "tenants/default.rego"
        for bundle in manifest["bundles"]:
            assert len(bundle["sha256"]) == 64
            int(bundle["sha256"], 16)  # valid hex

    def test_sha256_changes_when_policy_changes(self, tmp_path: Path):
        (tmp_path / "global").mkdir()
        (tmp_path / "global" / "a.rego").write_text("package scanner.global\n", encoding="utf-8")

        before = gen.build_manifest(tmp_path, version="1", git_commit="x")
        (tmp_path / "global" / "a.rego").write_text(
            "package scanner.global\n# changed\n", encoding="utf-8"
        )
        after = gen.build_manifest(tmp_path, version="1", git_commit="x")

        assert before["bundles"][0]["sha256"] != after["bundles"][0]["sha256"]

    def test_deterministic_for_same_content(self, tmp_path: Path):
        (tmp_path / "global").mkdir()
        (tmp_path / "global" / "a.rego").write_text("package scanner.global\n", encoding="utf-8")

        first = gen.build_manifest(tmp_path, version="1", git_commit="x")
        second = gen.build_manifest(tmp_path, version="1", git_commit="x")
        assert first["bundles"] == second["bundles"]

    def test_skips_hidden_and_bundles_dirs(self, tmp_path: Path):
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".hidden" / "h.rego").write_text("package h\n", encoding="utf-8")
        (tmp_path / "bundles").mkdir()
        (tmp_path / "bundles" / "b.rego").write_text("package b\n", encoding="utf-8")
        (tmp_path / "global").mkdir()
        (tmp_path / "global" / "a.rego").write_text("package scanner.global\n", encoding="utf-8")

        manifest = gen.build_manifest(tmp_path, version="1", git_commit="x")
        assert [b["id"] for b in manifest["bundles"]] == ["global"]

    def test_missing_dir_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            gen.build_manifest(tmp_path / "nope", version="1")

    def test_no_rego_raises(self, tmp_path: Path):
        with pytest.raises(RuntimeError):
            gen.build_manifest(tmp_path, version="1")


class TestCli:
    def test_main_writes_manifest(self, tmp_path: Path):
        policies = tmp_path / "policies"
        (policies / "global").mkdir(parents=True)
        (policies / "global" / "a.rego").write_text("package scanner.global\n", encoding="utf-8")

        rc = gen.main(
            [
                "--policies-dir", str(policies),
                "--output", str(policies / "manifest.json"),
                "--version", "3.1.4",
                "--git-commit", "deadbeef",
            ]
        )
        assert rc == 0
        data = json.loads((policies / "manifest.json").read_text(encoding="utf-8"))
        assert data["version"] == "3.1.4"
        assert data["git_commit"] == "deadbeef"

    def test_committed_baseline_manifest_is_valid(self):
        data = json.loads((POLICIES_DIR / "manifest.json").read_text(encoding="utf-8"))
        assert set(data) == {"version", "bundles", "created_at", "git_commit"}
        assert [b["id"] for b in data["bundles"]] == ["global", "tenants_default"]
        datetime.strptime(data["created_at"], "%Y-%m-%dT%H:%M:%SZ")
