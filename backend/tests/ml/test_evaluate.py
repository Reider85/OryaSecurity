"""
Tests for the evaluation pipeline (Prompt 20).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import numpy as np
import pytest

from app.ml.build_test_set import canonical_hash
from app.ml.evaluate import (
    ModelLoadError,
    _softmax,
    load_test_set,
    main,
    predict_all,
    resolve_runner,
    verify_lock,
)


def _examples(count=8):
    """Two examples per class."""
    return [
        {"id": f"v1-{i:04d}", "text": f"example text {i}", "label": i % 4, "source": "test"}
        for i in range(count)
    ]


def _write_locked_test_set(tmp_path, examples):
    output = tmp_path / "test_v1.json"
    manifest_path = tmp_path / "test_v1.manifest.json"
    output.write_text(json.dumps({"version": "v1", "examples": examples}), encoding="utf-8")
    counts = {"benign": 0, "prompt_injection": 0, "jailbreak": 0, "suspicious": 0}
    for example in examples:
        counts[list(counts)[example["label"]]] += 1
    manifest_path.write_text(json.dumps({
        "version": "v1",
        "num_examples": len(examples),
        "per_class": counts,
        "sha256": canonical_hash(examples),
    }), encoding="utf-8")
    return output, manifest_path


class FakeRunner:
    """Deterministic model stand-in."""

    def __init__(self, probs):
        self.probs = np.asarray(probs, dtype=float)

    def predict_proba(self, texts):
        return np.tile(self.probs, (len(texts), 1))

    def predict_one(self, text):
        return self.probs


class TestLoadTestSet:
    def test_valid_document(self, tmp_path):
        examples = _examples()
        path, _ = _write_locked_test_set(tmp_path, examples)
        assert load_test_set(path) == examples

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            load_test_set(tmp_path / "absent.json")

    def test_invalid_json_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not-json", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid JSON"):
            load_test_set(path)

    def test_empty_examples_raises(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text(json.dumps({"examples": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="no examples"):
            load_test_set(path)

    def test_empty_text_raises(self, tmp_path):
        path = tmp_path / "blank.json"
        path.write_text(json.dumps({"examples": [{"id": "1", "text": "  ", "label": 0}]}),
                        encoding="utf-8")
        with pytest.raises(ValueError, match="Empty text"):
            load_test_set(path)

    @pytest.mark.parametrize("label", [-1, 4, "1", None])
    def test_invalid_label_raises(self, tmp_path, label):
        path = tmp_path / "labels.json"
        path.write_text(json.dumps({"examples": [{"id": "1", "text": "ok", "label": label}]}),
                        encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid label"):
            load_test_set(path)


class TestVerifyLock:
    def test_returns_digest_when_locked(self, tmp_path):
        examples = _examples()
        path, manifest = _write_locked_test_set(tmp_path, examples)
        assert verify_lock(path, examples, manifest) == canonical_hash(examples)

    def test_default_manifest_path_is_used(self, tmp_path):
        examples = _examples()
        path, _ = _write_locked_test_set(tmp_path, examples)
        # manifest sits next to the test set with the default suffix
        assert verify_lock(path, examples, None) == canonical_hash(examples)

    def test_missing_manifest_raises(self, tmp_path):
        examples = _examples()
        path, _ = _write_locked_test_set(tmp_path, examples)
        (tmp_path / "test_v1.manifest.json").unlink()
        with pytest.raises(ValueError, match="not locked"):
            verify_lock(path, examples, None)

    def test_mismatch_raises(self, tmp_path):
        examples = _examples()
        path, manifest = _write_locked_test_set(tmp_path, examples)
        tampered = _examples()
        tampered[0]["text"] = "modified after locking"
        with pytest.raises(ValueError, match="Lock mismatch"):
            verify_lock(path, tampered, manifest)


class TestResolveRunner:
    def test_missing_path_raises(self, tmp_path):
        with pytest.raises(ModelLoadError, match="not found"):
            resolve_runner(tmp_path / "absent")

    def test_empty_dir_raises_with_hint(self, tmp_path):
        with pytest.raises(ModelLoadError, match="No model artifacts"):
            resolve_runner(tmp_path)

    def test_prefers_int8_onnx(self, tmp_path):
        (tmp_path / "model_int8.onnx").touch()
        (tmp_path / "model.onnx").touch()
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")
        (tmp_path / "model.safetensors").touch()

        with patch("app.ml.evaluate.OnnxRunner") as onnx_runner, \
                patch("app.ml.evaluate.HuggingFaceRunner") as hf_runner:
            resolve_runner(tmp_path)

        onnx_runner.assert_called_once_with(tmp_path / "model_int8.onnx", tmp_path, 512)
        hf_runner.assert_not_called()

    def test_falls_back_to_fp32_onnx(self, tmp_path):
        (tmp_path / "model.onnx").touch()
        with patch("app.ml.evaluate.OnnxRunner") as onnx_runner, \
                patch("app.ml.evaluate.HuggingFaceRunner"):
            resolve_runner(tmp_path)
        onnx_runner.assert_called_once_with(tmp_path / "model.onnx", tmp_path, 512)

    def test_falls_back_to_hf_model(self, tmp_path):
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")
        (tmp_path / "model.safetensors").touch()
        with patch("app.ml.evaluate.OnnxRunner") as onnx_runner, \
                patch("app.ml.evaluate.HuggingFaceRunner") as hf_runner:
            resolve_runner(tmp_path)
        onnx_runner.assert_not_called()
        hf_runner.assert_called_once_with(tmp_path, 512)

    def test_onnx_file_uses_parent_as_tokenizer_dir(self, tmp_path):
        model_file = tmp_path / "custom.onnx"
        model_file.touch()
        with patch("app.ml.evaluate.OnnxRunner") as onnx_runner, \
                patch("app.ml.evaluate.HuggingFaceRunner"):
            resolve_runner(model_file)
        onnx_runner.assert_called_once_with(model_file, tmp_path, 512)

    def test_config_without_weights_raises(self, tmp_path):
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ModelLoadError, match="No model artifacts"):
            resolve_runner(tmp_path)


class TestSoftmaxAndPredictAll:
    def test_softmax_rows_are_probabilities(self):
        probs = _softmax(np.array([[1.0, 2.0, 3.0], [1000.0, 1000.0, 1000.0]]))
        assert probs.shape == (2, 3)
        assert np.allclose(probs.sum(axis=1), 1.0)
        assert probs[0].argmax() == 2

    def test_predict_all_batches(self):
        runner = FakeRunner([0.7, 0.1, 0.1, 0.1])
        texts = [f"t{i}" for i in range(10)]
        probs = predict_all(runner, texts, batch_size=4)

        assert probs.shape == (10, 4)
        assert np.allclose(probs, np.tile(runner.probs, (10, 1)))


class TestMainExitCodes:
    def _run(self, tmp_path, examples, baseline=None, model_dir=None, **kwargs):
        test_set, manifest = _write_locked_test_set(tmp_path, examples)
        model_dir = model_dir or (tmp_path / "model")
        args = [
            "--model", str(model_dir),
            "--test-set", str(test_set),
            "--manifest", str(manifest),
            "--report", str(model_dir / "evaluation_report.json"),
            "--confusion-matrix", str(model_dir / "confusion_matrix.png"),
            "--max-samples", "5",
        ]
        if baseline is not None:
            args += ["--baseline", str(baseline)]
        args += [item for pair in kwargs.items() for item in (f"--{pair[0].replace('_', '-')}", str(pair[1]))]

        with patch("app.ml.evaluate.resolve_runner",
                   return_value=FakeRunner([0.7, 0.1, 0.1, 0.1])):
            return main(args), model_dir

    def test_success_writes_report(self, tmp_path, capsys):
        code, model_dir = self._run(tmp_path, _examples())

        assert code == 0
        report_path = model_dir / "evaluation_report.json"
        assert report_path.exists()
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["num_examples"] == 8
        assert data["gate"]["checked"] is False
        assert (model_dir / "confusion_matrix.png").exists()

        out = capsys.readouterr().out
        assert "ML Classifier Evaluation" in out
        assert "f1_macro" in out

    def test_regression_gate_fails_with_exit_one(self, tmp_path):
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({"f1_macro": 0.99}), encoding="utf-8")

        code, _ = self._run(tmp_path, _examples(), baseline=baseline)
        assert code == 1

    def test_passing_gate_with_sufficient_baseline(self, tmp_path):
        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps({"f1_macro": 0.01}), encoding="utf-8")

        code, model_dir = self._run(tmp_path, _examples(), baseline=baseline)
        assert code == 0
        data = json.loads((model_dir / "evaluation_report.json").read_text(encoding="utf-8"))
        assert data["gate"]["passed"] is True
        assert data["gate"]["checked"] is True

    def test_missing_model_returns_two(self, tmp_path):
        test_set, manifest = _write_locked_test_set(tmp_path, _examples())
        args = [
            "--model", str(tmp_path / "absent-model"),
            "--test-set", str(test_set),
            "--manifest", str(manifest),
        ]
        with patch("app.ml.evaluate.resolve_runner",
                   side_effect=ModelLoadError("no model")):
            assert main(args) == 2

    def test_broken_lock_returns_two_before_model_load(self, tmp_path):
        examples = _examples()
        test_set, manifest = _write_locked_test_set(tmp_path, examples)
        tampered = _examples()
        tampered[0]["text"] = "tampered"
        test_set.write_text(json.dumps({"examples": tampered}), encoding="utf-8")

        args = [
            "--model", str(tmp_path / "model"),
            "--test-set", str(test_set),
            "--manifest", str(manifest),
        ]
        with patch("app.ml.evaluate.resolve_runner") as resolve:
            assert main(args) == 2
        resolve.assert_not_called()
