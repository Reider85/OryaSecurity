from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.ml import export_onnx_int8, validate_onnx
from app.ml.validate_onnx import (
    DEFAULT_THRESHOLD,
    OnnxEmbedder,
    ValidationReport,
    cosine_similarity,
    default_prompts,
    evaluate_similarities,
    mean_pool_l2,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CPU_CONFIG = REPO_ROOT / "ml-models" / "embeddings" / "CPU" / "config.pbtxt"
INT8_MODEL = REPO_ROOT / "ml-models" / "embeddings" / "1" / "model_int8.onnx"
FP32_MODEL = REPO_ROOT / "ml-models" / "embeddings" / "1" / "model.onnx"
TOKENIZER_DIR = REPO_ROOT / "ml-models" / "embeddings" / "1"


class TestValidationPrompts:
    def test_exactly_100_prompts(self):
        prompts = default_prompts()
        assert len(prompts) == 100
        assert all(p and p.strip() for p in prompts)
        assert len(set(prompts)) == len(prompts), "prompts must be unique"

    def test_base_list_count_in_source(self):
        import ast

        src = Path(validate_onnx.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_BASE_PROMPTS":
                assert len(node.value.elts) == 100
                return
        pytest.fail("_BASE_PROMPTS not found")


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = np.array([1.0, 2.0, 3.0])
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = np.array([1.0, 0.0])
        b = np.array([0.0, 1.0])
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a = np.array([1.0, 0.0])
        b = np.array([-1.0, 0.0])
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector(self):
        assert cosine_similarity(np.zeros(3), np.ones(3)) == 0.0

    def test_accepts_lists(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


class TestMeanPoolL2:
    def test_masked_pooling_and_norm(self):
        hidden = np.array([[[1.0, 2.0], [3.0, 4.0], [0.0, 0.0]]])
        mask = np.array([[1, 1, 0]])
        result = mean_pool_l2(hidden, mask)
        # Mean pooling: [2, 3] → L2 → [2, 3]/sqrt(13)
        expected = np.array([[2.0 / np.sqrt(13), 3.0 / np.sqrt(13)]], dtype=np.float32)
        np.testing.assert_allclose(result, expected, rtol=1e-6)

    def test_output_is_unit_norm(self):
        rng = np.random.default_rng(42)
        hidden = rng.normal(size=(2, 5, 8)).astype(np.float32)
        mask = np.ones((2, 5), dtype=np.int64)
        result = mean_pool_l2(hidden, mask)
        assert result.shape == (2, 8)
        norms = np.linalg.norm(result, axis=1)
        np.testing.assert_allclose(norms, np.ones(2), rtol=1e-5)


class TestValidationReport:
    def test_passed_when_above_threshold(self):
        report = evaluate_similarities([0.96, 0.97, 0.98], threshold=0.95)
        assert report.passed is True
        assert report.mean_cosine == pytest.approx(0.97)
        assert report.min_cosine == pytest.approx(0.96)
        assert report.num_prompts == 3

    def test_failed_when_at_or_below_threshold(self):
        report = evaluate_similarities([0.90, 0.95], threshold=0.95)
        assert report.passed is False

    def test_empty_report_never_passes(self):
        report = evaluate_similarities([], threshold=0.95)
        assert report.passed is False
        assert report.mean_cosine == 0.0
        assert report.min_cosine == 0.0
        assert report.p05_cosine == 0.0

    def test_p05_percentile(self):
        data = [0.80] + [0.99] * 19
        report = evaluate_similarities(data, threshold=0.9)
        assert report.p05_cosine == pytest.approx(float(np.percentile(data, 5)))

    def test_to_dict_shape(self):
        report = evaluate_similarities([0.96], threshold=0.95)
        data = report.to_dict()
        assert data == {
            "num_prompts": 1,
            "threshold": 0.95,
            "mean_cosine": pytest.approx(0.96),
            "min_cosine": pytest.approx(0.96),
            "p05_cosine": pytest.approx(0.96),
            "passed": True,
        }
        data_full = report.to_dict(include_per_prompt=True)
        assert data_full["similarities"] == [0.96]

    def test_default_threshold_constant(self):
        assert DEFAULT_THRESHOLD == 0.95

    def test_report_is_dataclass_field_mutable_ok(self):
        report = ValidationReport(num_prompts=1, threshold=0.5)
        report.similarities.append(0.9)
        assert report.passed is True


class TestOnnxEmbedder:
    def _make_embedder(self, tmp_path, hidden_dim=4):
        model_path = tmp_path / "model.onnx"
        model_path.write_bytes(b"stub")
        tokenizer_dir = tmp_path / "tok"
        tokenizer_dir.mkdir()

        mock_session = MagicMock()
        mock_session.run.return_value = [
            np.array([[[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]], dtype=np.float32)
        ]
        mock_session.get_inputs.return_value = [MagicMock(name="input_ids"), MagicMock(name="attention_mask")]

        mock_tokenizer = MagicMock()
        mock_tokenizer.return_value = {
            "input_ids": np.array([[1, 2]]),
            "attention_mask": np.array([[1, 1]]),
        }

        with patch("app.ml.validate_onnx.ort") as mock_ort, patch(
            "app.ml.validate_onnx.AutoTokenizer"
        ) as mock_tok:
            mock_ort.InferenceSession.return_value = mock_session
            mock_tok.from_pretrained.return_value = mock_tokenizer
            embedder = OnnxEmbedder(model_path, tokenizer_dir)

        return embedder, mock_session, mock_tokenizer

    def test_init_creates_session_and_tokenizer(self, tmp_path):
        embedder, _, _ = self._make_embedder(tmp_path)
        assert embedder.session is not None
        assert embedder.prefix == "query: "
        assert embedder.max_length == 512

    def test_embed_pools_output(self, tmp_path):
        embedder, mock_session, mock_tokenizer = self._make_embedder(tmp_path)
        result = embedder.embed("hello")
        assert result.shape == (4,)
        # Hidden [1,0,0,0] + [0,1,0,0] → mean [0.5, 0.5, 0, 0] → L2 norm
        expected = np.array([1 / np.sqrt(2), 1 / np.sqrt(2), 0.0, 0.0], dtype=np.float32)
        np.testing.assert_allclose(result, expected, rtol=1e-5)
        mock_session.run.assert_called_once()
        feeds = mock_session.run.call_args[0][1]
        assert set(feeds) == {"input_ids", "attention_mask"}
        assert feeds["input_ids"].dtype == np.int64

    def test_embed_prefixes_prompt(self, tmp_path):
        embedder, _, mock_tokenizer = self._make_embedder(tmp_path)
        embedder.embed("hello")
        prefixed = mock_tokenizer.call_args[0][0]
        assert prefixed == "query: hello"


class TestValidate:
    def test_empty_prompts_raises(self, tmp_path):
        with pytest.raises(ValueError, match="empty"):
            validate_onnx.validate(tmp_path / "a.onnx", tmp_path / "b.onnx", tmp_path, prompts=[])

    def test_compare_paths(self, tmp_path):
        ref_vec = np.array([1.0, 0.0])
        int8_vec = np.array([1.0, 0.1])
        calls = {"n": 0}

        def fake_embedder_factory(*args, **kwargs):
            emb = MagicMock()
            calls["n"] += 1
            # OnnxEmbedder is instantiated twice (fp32 then int8)
            if calls["n"] % 2 == 1:
                emb.embed = lambda text: ref_vec
            else:
                emb.embed = lambda text: int8_vec
            return emb

        with patch.object(validate_onnx, "OnnxEmbedder", side_effect=fake_embedder_factory):
            report = validate_onnx.validate(
                "fp32.onnx", "int8.onnx", "tok", prompts=["a", "b"], threshold=0.95
            )
        assert report.num_prompts == 2
        assert report.mean_cosine == pytest.approx(cosine_similarity(ref_vec, int8_vec))
        assert report.passed is True


class TestValidateCli:
    def test_parser_defaults(self):
        parser = validate_onnx.build_parser()
        args = parser.parse_args(["--fp32", "a.onnx", "--int8", "b.onnx", "--tokenizer", "tok"])
        assert args.threshold == 0.95
        assert args.max_length == 512
        assert args.per_prompt is False

    def test_main_pass(self, tmp_path, capsys):
        report = evaluate_similarities([0.99], threshold=0.95)
        with patch.object(validate_onnx, "validate", return_value=report):
            rc = validate_onnx.main(
                ["--fp32", "a.onnx", "--int8", "b.onnx", "--tokenizer", "tok"]
            )
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["passed"] is True

    def test_main_fail_writes_report(self, tmp_path, capsys):
        report = evaluate_similarities([0.50], threshold=0.95)
        report_path = tmp_path / "report.json"
        with patch.object(validate_onnx, "validate", return_value=report):
            rc = validate_onnx.main(
                [
                    "--fp32", "a.onnx",
                    "--int8", "b.onnx",
                    "--tokenizer", "tok",
                    "--report", str(report_path),
                    "--per-prompt",
                ]
            )
        assert rc == 1
        saved = json.loads(report_path.read_text(encoding="utf-8"))
        assert saved["passed"] is False
        assert saved["similarities"] == [0.50]


def _patch_optimum_modules(monkeypatch, model_cls=None, quantizer_cls=None):
    """Install fake optimum modules into sys.modules for import-based patching."""
    ort_mod = MagicMock(name="optimum.onnxruntime")
    conf_mod = MagicMock(name="optimum.onnxruntime.configuration")
    if model_cls is not None:
        ort_mod.ORTModelForFeatureExtraction = model_cls
    if quantizer_cls is not None:
        ort_mod.ORTQuantizer = quantizer_cls
    monkeypatch.setitem(sys.modules, "optimum", MagicMock(name="optimum"))
    monkeypatch.setitem(sys.modules, "optimum.onnxruntime", ort_mod)
    monkeypatch.setitem(sys.modules, "optimum.onnxruntime.configuration", conf_mod)
    return ort_mod, conf_mod


class TestExportDeps:
    def test_missing_deps_raises(self, monkeypatch):
        import importlib

        real = importlib.import_module

        def fake_import(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("no torch")
            return real(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", fake_import)
        with pytest.raises(SystemExit, match="torch"):
            export_onnx_int8._require_export_deps()

    def test_deps_present_passes(self):
        # torch/optimum/onnx may or may not be installed; the call must not
        # raise when every import succeeds. If export extra is absent, verify
        # the SystemExit message lists all three.
        try:
            export_onnx_int8._require_export_deps()
        except SystemExit as exc:
            msg = str(exc)
            assert "backend[export]" in msg


class TestNormalizeOnnxContract:
    def _make_model(self, extra_output=False, output_name="pooler_output", missing_input=False):
        onnx = pytest.importorskip("onnx")
        from onnx import TensorProto, helper

        inputs = [helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["batch", "seq"])]
        if not missing_input:
            inputs.append(helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["batch", "seq"]))
        first_out = helper.make_tensor_value_info(output_name, TensorProto.FLOAT, ["batch", "seq", 4])
        outputs = [first_out]
        if extra_output:
            outputs.append(helper.make_tensor_value_info("last_hidden_state", TensorProto.FLOAT, ["batch", "seq", 4]))
        # raw=True so the initializer carries raw_data — external-data conversion
        # in onnx only moves raw_data tensors.
        init = helper.make_tensor(
            "hidden_init", TensorProto.FLOAT, [1], np.array([0.0], dtype=np.float32).tobytes(), raw=True
        )
        node = helper.make_node("Identity", ["hidden_init"], ["hidden"])
        graph = helper.make_graph([node], "g", inputs, outputs, initializer=[init])
        model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
        return model

    def test_renames_first_output_and_drops_extra(self, tmp_path):
        onnx = pytest.importorskip("onnx")
        model = self._make_model(extra_output=True, output_name="pooler_output")
        path = tmp_path / "model.onnx"
        onnx.save(model, str(path))

        export_onnx_int8.normalize_onnx_contract(path)

        reloaded = onnx.load(str(path))
        assert [o.name for o in reloaded.graph.output] == ["last_hidden_state"]

    def test_keeps_output_already_named(self, tmp_path):
        onnx = pytest.importorskip("onnx")
        model = self._make_model(output_name="last_hidden_state")
        path = tmp_path / "model.onnx"
        onnx.save(model, str(path))

        with patch("onnx.save", wraps=onnx.save) as mock_save:
            export_onnx_int8.normalize_onnx_contract(path)
        # Contract already OK → file is not rewritten (avoids >2GB corruption)
        mock_save.assert_not_called()
        reloaded = onnx.load(str(path))
        assert [o.name for o in reloaded.graph.output] == ["last_hidden_state"]

    def test_large_model_uses_external_data(self, tmp_path, monkeypatch):
        onnx = pytest.importorskip("onnx")
        monkeypatch.setattr(export_onnx_int8, "EXTERNAL_DATA_THRESHOLD", 1)
        model = self._make_model(extra_output=True, output_name="pooler_output")
        path = tmp_path / "model.onnx"
        onnx.save(model, str(path))

        export_onnx_int8.normalize_onnx_contract(path)

        assert (tmp_path / "model.onnx_data").exists()
        reloaded = onnx.load(str(path))
        assert [o.name for o in reloaded.graph.output] == ["last_hidden_state"]

    def test_missing_required_input_raises(self, tmp_path):
        onnx = pytest.importorskip("onnx")
        model = self._make_model(missing_input=True)
        path = tmp_path / "model.onnx"
        onnx.save(model, str(path))

        with pytest.raises(RuntimeError, match="attention_mask"):
            export_onnx_int8.normalize_onnx_contract(path)


def _patch_tokenizer(monkeypatch):
    from transformers import AutoTokenizer

    mock_tok = MagicMock()
    monkeypatch.setattr(AutoTokenizer, "from_pretrained", staticmethod(lambda *a, **k: mock_tok))
    return mock_tok


class TestExportFp32:
    def test_export_success_without_optimization_result(self, tmp_path, monkeypatch):
        ort_mod, conf_mod = _patch_optimum_modules(monkeypatch)
        _patch_tokenizer(monkeypatch)

        def save_pretrained(dir_str):
            d = Path(dir_str)
            d.mkdir(parents=True, exist_ok=True)
            (d / "model.onnx").write_bytes(b"fp32-stub")

        ort_mod.ORTModelForFeatureExtraction.from_pretrained.return_value.save_pretrained.side_effect = save_pretrained
        # optimizer produces nothing in temp dir → keep unoptimized file
        ort_mod.ORTOptimizer.from_pretrained.return_value.optimize.side_effect = None

        version_dir = tmp_path / "1"
        with patch.object(export_onnx_int8, "normalize_onnx_contract") as mock_norm:
            path = export_onnx_int8.export_fp32("some/model", version_dir)

        assert path == version_dir / "model.onnx"
        assert path.read_bytes() == b"fp32-stub"
        mock_norm.assert_called_once_with(path)
        ort_mod.ORTModelForFeatureExtraction.from_pretrained.assert_called_once_with(
            "some/model", export=True
        )

    def test_export_promotes_optimized_file(self, tmp_path, monkeypatch):
        ort_mod, conf_mod = _patch_optimum_modules(monkeypatch)
        _patch_tokenizer(monkeypatch)

        def save_pretrained(dir_str):
            d = Path(dir_str)
            d.mkdir(parents=True, exist_ok=True)
            (d / "model.onnx").write_bytes(b"unoptimized")

        ort_mod.ORTModelForFeatureExtraction.from_pretrained.return_value.save_pretrained.side_effect = save_pretrained

        def fake_optimize(optimization_config=None, save_dir=None, file_suffix=None):
            d = Path(save_dir)
            d.mkdir(parents=True, exist_ok=True)
            (d / "model_optimized.onnx").write_bytes(b"optimized")
            (d / "model_optimized.onnx.data").write_bytes(b"opt-data")

        ort_mod.ORTOptimizer.from_pretrained.return_value.optimize.side_effect = fake_optimize

        version_dir = tmp_path / "1"
        fake_proto = MagicMock()
        fake_proto.graph.initializer = []
        fake_proto.graph.node = []

        with patch.object(export_onnx_int8, "normalize_onnx_contract"), \
                patch.object(export_onnx_int8, "_smoke_test_model") as mock_smoke, \
                patch("onnx.load", return_value=fake_proto), \
                patch("onnx.save"):
            path = export_onnx_int8.export_fp32("some/model", version_dir)

        mock_smoke.assert_called_once_with(path)
        assert path.read_bytes() == b"optimized"
        assert (version_dir / "model.onnx_data").read_bytes() == b"opt-data"
        assert not (version_dir / "model_optimized.onnx.data").exists()

    def test_promote_rolls_back_on_smoke_failure(self, tmp_path, monkeypatch):
        ort_mod, conf_mod = _patch_optimum_modules(monkeypatch)
        _patch_tokenizer(monkeypatch)

        def save_pretrained(dir_str):
            d = Path(dir_str)
            d.mkdir(parents=True, exist_ok=True)
            (d / "model.onnx").write_bytes(b"unoptimized")

        ort_mod.ORTModelForFeatureExtraction.from_pretrained.return_value.save_pretrained.side_effect = save_pretrained

        def fake_optimize(optimization_config=None, save_dir=None, file_suffix=None):
            d = Path(save_dir)
            d.mkdir(parents=True, exist_ok=True)
            (d / "model_optimized.onnx").write_bytes(b"optimized")

        ort_mod.ORTOptimizer.from_pretrained.return_value.optimize.side_effect = fake_optimize

        version_dir = tmp_path / "1"
        fake_proto = MagicMock()
        fake_proto.graph.initializer = []
        fake_proto.graph.node = []

        with patch.object(export_onnx_int8, "normalize_onnx_contract"), \
                patch.object(export_onnx_int8, "_smoke_test_model", side_effect=RuntimeError("unloadable")), \
                patch("onnx.load", return_value=fake_proto), \
                patch("onnx.save"):
            path = export_onnx_int8.export_fp32("some/model", version_dir)

        # Promoted file failed the ORT smoke test → original export restored
        assert path.read_bytes() == b"unoptimized"

    def test_optimize_failure_is_non_fatal(self, tmp_path, monkeypatch):
        ort_mod, conf_mod = _patch_optimum_modules(monkeypatch)
        _patch_tokenizer(monkeypatch)

        def save_pretrained(dir_str):
            d = Path(dir_str)
            d.mkdir(parents=True, exist_ok=True)
            (d / "model.onnx").write_bytes(b"unoptimized")

        ort_mod.ORTModelForFeatureExtraction.from_pretrained.return_value.save_pretrained.side_effect = save_pretrained
        ort_mod.ORTOptimizer.from_pretrained.return_value.optimize.side_effect = RuntimeError("boom")

        version_dir = tmp_path / "1"
        with patch.object(export_onnx_int8, "normalize_onnx_contract"):
            path = export_onnx_int8.export_fp32("some/model", version_dir)
        assert path.read_bytes() == b"unoptimized"

    def test_missing_model_file_raises(self, tmp_path, monkeypatch):
        ort_mod, conf_mod = _patch_optimum_modules(monkeypatch)
        ort_mod.ORTModelForFeatureExtraction.from_pretrained.return_value.save_pretrained.side_effect = lambda d: None

        with pytest.raises(RuntimeError, match="did not produce"):
            export_onnx_int8.export_fp32("some/model", tmp_path / "1")


class TestQuantizeInt8:
    def test_quantize_success(self, tmp_path):
        from onnx import TensorProto
        from onnxruntime.quantization import QuantType

        version_dir = tmp_path / "1"
        version_dir.mkdir()
        fp32 = version_dir / "model.onnx"
        fp32.write_bytes(b"fp32")

        def fake_quantize_dynamic(model_input=None, model_output=None, **kwargs):
            Path(model_output).write_bytes(b"int8-stub")

        with patch("onnxruntime.quantization.quantize_dynamic", side_effect=fake_quantize_dynamic) as mock_q, \
                patch.object(export_onnx_int8, "normalize_onnx_contract") as mock_norm:
            path = export_onnx_int8.quantize_int8(fp32, version_dir)

        assert path == version_dir / "model_int8.onnx"
        assert path.read_bytes() == b"int8-stub"
        mock_norm.assert_called_once_with(path)

        kwargs = mock_q.call_args.kwargs
        assert kwargs["model_input"] == str(fp32)
        assert kwargs["weight_type"] == QuantType.QInt8
        assert kwargs["per_channel"] is False
        assert kwargs["extra_options"] == {"DefaultTensorType": TensorProto.FLOAT}

    def test_quantize_no_output_raises(self, tmp_path):
        version_dir = tmp_path / "1"
        version_dir.mkdir()
        fp32 = version_dir / "model.onnx"
        fp32.write_bytes(b"fp32")

        with patch("onnxruntime.quantization.quantize_dynamic", return_value=None):
            with pytest.raises(RuntimeError, match="no ONNX file"):
                export_onnx_int8.quantize_int8(fp32, version_dir)


class TestRunValidation:
    def test_returns_true_on_pass(self):
        report = evaluate_similarities([0.99], threshold=0.95)
        with patch("app.ml.validate_onnx.validate", return_value=report):
            assert export_onnx_int8.run_validation(Path("a"), Path("b"), Path("c"), 0.95) is True

    def test_returns_false_on_fail(self):
        report = evaluate_similarities([0.5], threshold=0.95)
        with patch("app.ml.validate_onnx.validate", return_value=report):
            assert export_onnx_int8.run_validation(Path("a"), Path("b"), Path("c"), 0.95) is False


class TestExportEmbeddings:
    def test_happy_path(self, tmp_path):
        int8 = tmp_path / "1" / "model_int8.onnx"
        with patch.object(export_onnx_int8, "_require_export_deps"), patch.object(
            export_onnx_int8, "export_fp32", return_value=tmp_path / "1" / "model.onnx"
        ) as mock_export, patch.object(
            export_onnx_int8, "quantize_int8", return_value=int8
        ) as mock_quant, patch.object(export_onnx_int8, "run_validation", return_value=True) as mock_val:
            result = export_onnx_int8.export_embeddings(output_dir=tmp_path)

        assert result == int8
        mock_export.assert_called_once()
        mock_quant.assert_called_once()
        mock_val.assert_called_once()

    def test_validation_failure_exits(self, tmp_path):
        with patch.object(export_onnx_int8, "_require_export_deps"), patch.object(
            export_onnx_int8, "export_fp32", return_value=tmp_path / "model.onnx"
        ), patch.object(
            export_onnx_int8, "quantize_int8", return_value=tmp_path / "model_int8.onnx"
        ), patch.object(export_onnx_int8, "run_validation", return_value=False):
            with pytest.raises(SystemExit, match="validation failed"):
                export_onnx_int8.export_embeddings(output_dir=tmp_path)

    def test_skip_validate(self, tmp_path):
        int8 = tmp_path / "model_int8.onnx"
        with patch.object(export_onnx_int8, "_require_export_deps"), patch.object(
            export_onnx_int8, "export_fp32", return_value=tmp_path / "model.onnx"
        ), patch.object(
            export_onnx_int8, "quantize_int8", return_value=int8
        ), patch.object(export_onnx_int8, "run_validation") as mock_val:
            result = export_onnx_int8.export_embeddings(output_dir=tmp_path, skip_validate=True)
        assert result == int8
        mock_val.assert_not_called()


class TestExportCli:
    def test_parser_defaults(self):
        parser = export_onnx_int8.build_parser()
        args = parser.parse_args([])
        assert args.model_id == "intfloat/multilingual-e5-large"
        assert args.opset == 17
        assert args.threshold == 0.95
        assert args.skip_validate is False
        assert export_onnx_int8.DEFAULT_OUTPUT_DIR.parts[-2:] == ("ml-models", "embeddings")

    def test_main_delegates(self):
        with patch.object(export_onnx_int8, "export_embeddings") as mock_run:
            rc = export_onnx_int8.main(["--model-id", "x/y", "--skip-validate"])
        assert rc == 0
        mock_run.assert_called_once_with(
            model_id="x/y",
            output_dir=str(export_onnx_int8.DEFAULT_OUTPUT_DIR),
            opset=17,
            validate_threshold=0.95,
            skip_validate=True,
        )

    def test_repo_root_points_at_project(self):
        assert export_onnx_int8.REPO_ROOT.name == "OryaSecurity"
        assert (export_onnx_int8.REPO_ROOT / "backend").is_dir()


class TestConfigPaths:
    def test_settings_use_versioned_layout(self):
        from app.config import settings

        assert settings.embedding_model_path == "ml-models/embeddings/1/model_int8.onnx"
        assert settings.embedding_tokenizer_path == "ml-models/embeddings/1"

    def test_cpu_config_exists_and_contract(self):
        assert CPU_CONFIG.exists(), f"missing {CPU_CONFIG}"
        content = CPU_CONFIG.read_text(encoding="utf-8")
        assert "KIND_CPU" in content
        assert "input_ids" in content
        assert "attention_mask" in content
        assert "last_hidden_state" in content
        assert "TYPE_INT64" in content
        assert "TYPE_FP32" in content


@pytest.mark.integration
@pytest.mark.skipif(not INT8_MODEL.exists(), reason="int8 model not exported yet")
class TestRealModelIntegration:
    """Runs only after `python -m app.ml.export_onnx_int8` has been executed."""

    def test_int8_model_loads_and_embeds(self):
        embedder = OnnxEmbedder(INT8_MODEL, TOKENIZER_DIR)
        vec = embedder.embed("What is the capital of France?")
        assert vec.shape == (1024,)
        assert vec.dtype == np.float32
        assert np.linalg.norm(vec) == pytest.approx(1.0, abs=1e-3)

    @pytest.mark.skipif(not FP32_MODEL.exists(), reason="FP32 model not present")
    def test_int8_matches_fp32_on_single_prompt(self):
        fp32 = OnnxEmbedder(FP32_MODEL, TOKENIZER_DIR)
        int8 = OnnxEmbedder(INT8_MODEL, TOKENIZER_DIR)
        text = "Explain how photosynthesis works."
        sim = cosine_similarity(fp32.embed(text), int8.embed(text))
        assert sim > 0.95
