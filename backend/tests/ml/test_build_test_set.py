"""
Tests for the locked hold-out test set builder (Prompt 20).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.ml.build_test_set import (
    HOLDOUT_SUSPICIOUS_SEEDS,
    _dedup,
    build,
    build_manifest,
    canonical_hash,
    main,
    select_examples,
    text_hash,
    verify,
)
from app.ml.training_data import CLASS_LABELS, TrainingData


def _items(texts, label, source="fixture"):
    return [{"text": text, "label": label, "source": source} for text in texts]


def _pools(candidates_per_class=30):
    return {
        label: _items([f"class{label}-candidate-{i}" for i in range(candidates_per_class)], label)
        for label in range(4)
    }


class TestHashing:
    def test_text_hash_normalizes_whitespace(self):
        assert text_hash("  hello ") == text_hash("hello")

    def test_canonical_hash_is_key_order_independent(self):
        a = [{"id": "1", "text": "x", "label": 0}]
        b = [{"label": 0, "text": "x", "id": "1"}]
        assert canonical_hash(a) == canonical_hash(b)

    def test_canonical_hash_changes_with_content(self):
        a = [{"text": "x", "label": 0}]
        b = [{"text": "y", "label": 0}]
        assert canonical_hash(a) != canonical_hash(b)

    def test_dedup_drops_repeated_texts(self):
        pool = [
            {"text": "same", "label": 1},
            {"text": "same", "label": 1},
            {"text": "other", "label": 1},
        ]
        assert len(_dedup(pool)) == 2


class TestSelectExamples:
    def test_stratified_counts_and_ids(self):
        per_class = 5
        selected = select_examples(_pools(), per_class, seed=42, excluded={})

        assert len(selected) == per_class * 4
        for label in range(4):
            class_examples = [ex for ex in selected if ex["label"] == label]
            assert len(class_examples) == per_class

        # canonical output order: by class, then content hash
        keys = [(ex["label"], text_hash(ex["text"])) for ex in selected]
        assert keys == sorted(keys)
        assert [ex["id"] for ex in selected] == [f"v1-{i:04d}" for i in range(len(selected))]

    def test_deterministic_for_same_seed(self):
        first = select_examples(_pools(), 5, seed=42, excluded={})
        second = select_examples(_pools(), 5, seed=42, excluded={})
        assert first == second

    def test_seed_changes_selection(self):
        first = select_examples(_pools(30), 5, seed=1, excluded={})
        second = select_examples(_pools(30), 5, seed=2, excluded={})
        assert [ex["text"] for ex in first] != [ex["text"] for ex in second]

    def test_excluded_texts_are_skipped(self):
        pools = _pools()
        excluded = {text_hash("class2-candidate-0"): "train"}
        selected = select_examples(pools, 5, seed=42, excluded=excluded)
        assert "class2-candidate-0" not in [ex["text"] for ex in selected]

    def test_insufficient_candidates_raises(self):
        pools = {label: _items([f"only-{label}"], label) for label in range(4)}
        with pytest.raises(ValueError, match="Not enough hold-out candidates"):
            select_examples(pools, 5, seed=42, excluded={})

    def test_holdout_suspicious_seeds_are_distinct_from_training(self):
        from app.ml.training_data import SUSPICIOUS_SEEDS

        overlap = set(HOLDOUT_SUSPICIOUS_SEEDS) & set(SUSPICIOUS_SEEDS)
        assert not overlap


class TestBuildAndVerify:
    async def test_build_end_to_end_without_download(self, tmp_path):
        splits_dir = tmp_path / "splits"
        splits_dir.mkdir()
        # One benign fixture text is part of the training split -> excluded.
        (splits_dir / "train.jsonl").write_text(
            json.dumps({"text": "benign-shared", "label": 0}) + "\n",
            encoding="utf-8",
        )

        advbench = _items([f"adv-{i}" for i in range(20)], CLASS_LABELS["prompt_injection"])
        jbb = _items([f"jbb-{i}" for i in range(20)], CLASS_LABELS["jailbreak"])
        dolly = _items(["benign-shared"] + [f"benign-{i}" for i in range(19)], CLASS_LABELS["benign"])

        per_class = 4
        with patch.object(TrainingData, "load_advbench", new=AsyncMock(return_value=advbench)), \
                patch.object(TrainingData, "load_jailbreakbench", new=AsyncMock(return_value=jbb)), \
                patch.object(TrainingData, "load_dolly_benign", new=AsyncMock(return_value=dolly)):
            test_set = await build(
                output_path=tmp_path / "test_v1.json",
                splits_dir=splits_dir,
                per_class=per_class,
                seed=42,
                download=False,
            )

        assert len(test_set["examples"]) == per_class * 4
        assert test_set["counts"] == {
            "benign": per_class,
            "prompt_injection": per_class,
            "jailbreak": per_class,
            "suspicious": per_class,
        }
        # Disjoint with the training splits
        texts = [ex["text"] for ex in test_set["examples"]]
        assert "benign-shared" not in texts

        manifest = build_manifest(test_set, splits_dir, tmp_path / "test_v1.json")
        assert manifest["sha256"] == canonical_hash(test_set["examples"])
        assert manifest["num_examples"] == len(test_set["examples"])
        assert "train.jsonl" in manifest["splits_sha256"]

        # Round-trip: write and verify
        output = tmp_path / "test_v1.json"
        output.write_text(json.dumps(test_set), encoding="utf-8")
        manifest_path = tmp_path / "test_v1.manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = verify(output, manifest_path, splits_dir)
        assert result["num_examples"] == per_class * 4

    def test_verify_detects_tampered_test_set(self, tmp_path):
        examples = [{"id": "v1-0000", "text": "hello", "label": 0, "source": "x"}]
        manifest = {
            "version": "v1",
            "num_examples": 1,
            "per_class": {"benign": 1, "prompt_injection": 0, "jailbreak": 0, "suspicious": 0},
            "sha256": canonical_hash(examples),
        }
        output = tmp_path / "test_v1.json"
        manifest_path = tmp_path / "test_v1.manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        splits_dir = tmp_path / "splits"
        splits_dir.mkdir()

        output.write_text(json.dumps({"examples": examples}), encoding="utf-8")
        assert verify(output, manifest_path, splits_dir)["num_examples"] == 1

        tampered = [{"id": "v1-0000", "text": "modified", "label": 0, "source": "x"}]
        output.write_text(json.dumps({"examples": tampered}), encoding="utf-8")
        with pytest.raises(ValueError, match="Lock mismatch"):
            verify(output, manifest_path, splits_dir)

    def test_verify_missing_files_raise(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            verify(tmp_path / "missing.json", tmp_path / "missing.manifest.json", tmp_path)

    def test_verify_detects_overlap_with_splits(self, tmp_path):
        examples = [{"id": "v1-0000", "text": "leaked", "label": 0, "source": "x"}]
        manifest = {
            "version": "v1",
            "num_examples": 1,
            "per_class": {"benign": 1, "prompt_injection": 0, "jailbreak": 0, "suspicious": 0},
            "sha256": canonical_hash(examples),
        }
        output = tmp_path / "test_v1.json"
        output.write_text(json.dumps({"examples": examples}), encoding="utf-8")
        manifest_path = tmp_path / "test_v1.manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        splits_dir = tmp_path / "splits"
        splits_dir.mkdir()
        (splits_dir / "val.jsonl").write_text(
            json.dumps({"text": "leaked", "label": 0}) + "\n", encoding="utf-8"
        )

        with pytest.raises(ValueError, match="overlaps training splits"):
            verify(output, manifest_path, splits_dir)


class TestCli:
    def test_check_returns_1_when_files_missing(self, tmp_path, capsys):
        code = main([
            "--check",
            "--output", str(tmp_path / "nope.json"),
            "--manifest", str(tmp_path / "nope.manifest.json"),
        ])
        assert code == 1
        assert "ERROR" in capsys.readouterr().err

    def test_check_returns_0_for_valid_lock(self, tmp_path, capsys):
        examples = [{"id": "v1-0000", "text": "hello", "label": 0, "source": "x"}]
        output = tmp_path / "test_v1.json"
        manifest_path = tmp_path / "test_v1.manifest.json"
        output.write_text(json.dumps({"examples": examples}), encoding="utf-8")
        manifest_path.write_text(json.dumps({
            "version": "v1",
            "num_examples": 1,
            "per_class": {"benign": 1, "prompt_injection": 0, "jailbreak": 0, "suspicious": 0},
            "sha256": canonical_hash(examples),
        }), encoding="utf-8")
        (tmp_path / "splits").mkdir()

        code = main([
            "--check",
            "--output", str(output),
            "--manifest", str(manifest_path),
            "--splits-dir", str(tmp_path / "splits"),
        ])
        assert code == 0
        assert '"status": "ok"' in capsys.readouterr().out
