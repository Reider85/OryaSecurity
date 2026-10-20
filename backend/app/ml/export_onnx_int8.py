"""Export intfloat/multilingual-e5-large to ONNX (FP32 optimized + int8 dynamic).

Pipeline (Prompt 8 / EP-08):
  1. Load the HuggingFace model and export it to ONNX (optimum, opset 17).
  2. Optimize the FP32 graph for CPU inference (ORTOptimizer, best-effort).
  3. Normalize the graph contract: inputs input_ids/attention_mask (INT64),
     single output ``last_hidden_state`` (FP32, [batch, seq, 1024]) — matches
     Triton config.pbtxt and the local EmbeddingClient.
  4. Dynamic int8 quantization (ORTQuantizer, avx2 kernels).
  5. Validate: mean cosine similarity vs FP32 on 100 test prompts > 0.95.

Artifacts (versioned Triton layout):
  ml-models/embeddings/1/model.onnx          # FP32 (optimized)
  ml-models/embeddings/1/model_int8.onnx     # int8 (CPU fallback)
  ml-models/embeddings/1/tokenizer.json, ... # tokenizer files

Usage:
    pip install -e "backend[export]"
    python -m app.ml.export_onnx_int8 [--model-id ...] [--output-dir ...]

Requires the ``export`` extra (torch + optimum + onnx).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

import structlog

logger = structlog.get_logger()

DEFAULT_MODEL_ID = "intfloat/multilingual-e5-large"
DEFAULT_OPSET = 17
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "ml-models" / "embeddings"

FP32_FILENAME = "model.onnx"
INT8_FILENAME = "model_int8.onnx"
EXTERNAL_DATA_FILENAME = "model.onnx_data"
# Single-proto protobuf limit is 2 GB; above this size external data is required.
EXTERNAL_DATA_THRESHOLD = 1_500_000_000


def _require_export_deps() -> None:
    """Fail with a clear message when the export extra is not installed."""
    import importlib

    missing = []
    for module in ("torch", "optimum", "onnx"):
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(module)
    if missing:
        raise SystemExit(
            f"Missing export dependencies: {', '.join(missing)}. "
            'Install with: pip install -e "backend[export]"'
        )


def normalize_onnx_contract(model_path: Path) -> None:
    """Enforce the Triton I/O contract on an exported ONNX graph.

    - First graph output is renamed to ``last_hidden_state`` when needed.
    - Extra graph outputs (e.g. pooler_output) are dropped so the graph
      matches ml-models/embeddings/config.pbtxt exactly.
    - Inputs input_ids/attention_mask (INT64) must exist; token_type_ids is
      left as-is when present (optional input with initializer).
    - When nothing needs to change the file is left untouched (important for
      multi-GB models — rewriting a >2 GB single proto corrupts it).
    - When a rewrite is required, models above the protobuf size limit are
      re-saved with external data (``model.onnx_data``).
    """
    import onnx

    # Cheap structural check — do not pull multi-GB tensor data into memory.
    model = onnx.load(str(model_path), load_external_data=False)

    input_names = [i.name for i in model.graph.input]
    for required in ("input_ids", "attention_mask"):
        if required not in input_names:
            raise RuntimeError(f"exported model is missing required input '{required}' (got {input_names})")

    outputs = model.graph.output
    if not outputs:
        raise RuntimeError("exported model has no graph outputs")

    changed = False
    if outputs[0].name != "last_hidden_state":
        logger.info("renaming_output", old=outputs[0].name, new="last_hidden_state")
        outputs[0].name = "last_hidden_state"
        changed = True
    if len(outputs) > 1:
        logger.info("dropping_extra_outputs", dropped=[o.name for o in outputs[1:]])
        del model.graph.output[1:]
        changed = True

    if not changed:
        logger.info(
            "onnx_contract_ok",
            path=str(model_path),
            inputs=input_names,
            outputs=[o.name for o in model.graph.output],
        )
        return

    # Rewrite with external data when the model exceeds the protobuf limit.
    model = onnx.load(str(model_path))
    full_outputs = model.graph.output
    full_outputs[0].name = "last_hidden_state"
    if len(full_outputs) > 1:
        del model.graph.output[1:]

    if model_path.stat().st_size > EXTERNAL_DATA_THRESHOLD:
        data_path = model_path.parent / EXTERNAL_DATA_FILENAME
        for stale in (model_path, data_path):
            if stale.exists():
                stale.unlink()
        onnx.save_model(
            model,
            str(model_path),
            save_as_external_data=True,
            all_tensors_to_one_file=True,
            location=EXTERNAL_DATA_FILENAME,
            size_threshold=0,
        )
        logger.info("onnx_saved_external_data", path=str(model_path), data=EXTERNAL_DATA_FILENAME)
    else:
        onnx.save(model, str(model_path))
        logger.info("onnx_saved", path=str(model_path))

    logger.info(
        "onnx_contract_normalized",
        path=str(model_path),
        inputs=input_names,
        outputs=[o.name for o in model.graph.output],
    )


def _smoke_test_model(model_path: Path) -> None:
    """Load the model into an ORT CPU session — catches broken graphs early."""
    import onnxruntime as ort

    ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])


def export_fp32(model_id: str, version_dir: Path, opset: int = DEFAULT_OPSET) -> Path:
    """Export the HF model to an optimized FP32 ONNX file in ``version_dir``.

    Returns the path to model.onnx. Tokenizer/config files are saved next
    to the model. The ORT graph-optimization promotion is verified with a
    real session load and rolled back to the unoptimized export on failure.
    """
    import onnx
    from optimum.onnxruntime import ORTModelForFeatureExtraction, ORTOptimizer
    from optimum.onnxruntime.configuration import OptimizationConfig
    from transformers import AutoTokenizer

    version_dir.mkdir(parents=True, exist_ok=True)

    logger.info("exporting_fp32", model_id=model_id, version_dir=str(version_dir), opset=opset)
    model = ORTModelForFeatureExtraction.from_pretrained(model_id, export=True)
    model.save_pretrained(str(version_dir))

    fp32_path = version_dir / FP32_FILENAME
    if not fp32_path.exists():
        raise RuntimeError(f"optimum export did not produce {fp32_path}")

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    tokenizer.save_pretrained(str(version_dir))

    # Best-effort graph optimization — keep the unoptimized file on failure.
    backups: dict[Path, Path] = {}
    try:
        logger.info("optimizing_fp32", level=99)
        optimizer = ORTOptimizer.from_pretrained(str(version_dir))
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            opt_dir = tmp / "optimized"
            optimizer.optimize(
                optimization_config=OptimizationConfig(optimization_level=99),
                save_dir=str(opt_dir),
                file_suffix="optimized",
            )
            optimized = opt_dir / "model_optimized.onnx"
            if optimized.exists():
                # Snapshot the original export so we can roll back if the
                # promoted optimized graph turns out unloadable. Backups live
                # in version_dir — a TemporaryDirectory would be cleaned up
                # before the except handler could restore from it.
                for f in (fp32_path, version_dir / EXTERNAL_DATA_FILENAME):
                    if f.exists():
                        backup = f.with_name(f.name + ".backup")
                        shutil.copyfile(f, backup)
                        backups[f] = backup

                # The external-data location is embedded in the proto, so a
                # plain copy of the .onnx file would break references — move
                # the data file under its original name instead. Depending on
                # the optimum/ORT version the data file is either
                # "model_optimized.onnx.data" or "model_optimized.onnx_data".
                data_candidates = [
                    opt_dir / "model_optimized.onnx.data",
                    opt_dir / "model_optimized.onnx_data",
                ]
                opt_data = next((c for c in data_candidates if c.exists()), None)
                target_data = version_dir / "model_optimized.onnx.data"
                for stale in (fp32_path, version_dir / EXTERNAL_DATA_FILENAME, target_data):
                    if stale.exists():
                        stale.unlink()
                shutil.move(str(optimized), str(fp32_path))
                if opt_data is not None:
                    shutil.move(str(opt_data), str(target_data))

                # Rewrite location refs to the stable name and verify the
                # result actually loads in onnxruntime.
                proto = onnx.load(str(fp32_path), load_external_data=False)
                tensors = list(proto.graph.initializer)
                for node in proto.graph.node:
                    for attr in node.attribute:
                        if attr.type == onnx.AttributeProto.TENSOR:
                            tensors.append(attr.t)
                        elif attr.type == onnx.AttributeProto.TENSORS:
                            tensors.extend(attr.tensors)
                for tensor in tensors:
                    if tensor.data_location == onnx.TensorProto.EXTERNAL:
                        for entry in tensor.external_data:
                            if entry.key == "location":
                                entry.value = EXTERNAL_DATA_FILENAME
                onnx.save(proto, str(fp32_path))
                if target_data.exists():
                    target_data.rename(version_dir / EXTERNAL_DATA_FILENAME)

                _smoke_test_model(fp32_path)
                logger.info("fp32_optimized", path=str(fp32_path))
    except Exception as exc:  # noqa: BLE001 — optimization is optional
        logger.warning("fp32_optimize_failed", error=str(exc), fallback="unoptimized")
        for stray_name in ("model_optimized.onnx.data", "model_optimized.onnx_data"):
            stray = version_dir / stray_name
            if stray.exists():
                stray.unlink()
        for original, backup in backups.items():
            shutil.copyfile(backup, original)
    finally:
        for backup in backups.values():
            if backup.exists():
                backup.unlink()

    normalize_onnx_contract(fp32_path)
    return fp32_path


def quantize_int8(fp32_path: Path, version_dir: Path) -> Path:
    """Dynamic int8 quantization of the FP32 model (onnxruntime backend).

    Uses ``onnxruntime.quantization.quantize_dynamic`` directly — the same
    backend as ``optimum-cli quantize --onnxruntime`` — with the
    ``DefaultTensorType`` workaround required for ORT-optimized graphs
    whose shape inference cannot type intermediate MatMul outputs.

    Returns the path to model_int8.onnx inside ``version_dir``.
    """
    from onnx import TensorProto
    from onnxruntime.quantization import QuantType, quantize_dynamic

    logger.info("quantizing_int8", source=str(fp32_path))
    int8_path = version_dir / INT8_FILENAME
    with tempfile.TemporaryDirectory() as tmp:
        tmp_out = Path(tmp) / "model_quantized.onnx"
        quantize_dynamic(
            model_input=str(fp32_path),
            model_output=str(tmp_out),
            per_channel=False,
            weight_type=QuantType.QInt8,
            extra_options={"DefaultTensorType": TensorProto.FLOAT},
        )
        if not tmp_out.exists():
            raise RuntimeError(f"quantizer produced no ONNX file in {tmp}")
        shutil.copyfile(tmp_out, int8_path)

    normalize_onnx_contract(int8_path)
    logger.info("int8_ready", path=str(int8_path), size_mb=round(int8_path.stat().st_size / 1e6, 1))
    return int8_path


def run_validation(fp32_path: Path, int8_path: Path, version_dir: Path, threshold: float) -> bool:
    """Run the FP32-vs-int8 cosine similarity check. Returns True on pass."""
    from app.ml.validate_onnx import validate

    report = validate(fp32_path, int8_path, version_dir, threshold=threshold)
    logger.info(
        "validation_report",
        num_prompts=report.num_prompts,
        mean_cosine=round(report.mean_cosine, 4),
        min_cosine=round(report.min_cosine, 4),
        p05_cosine=round(report.p05_cosine, 4),
        threshold=report.threshold,
        passed=report.passed,
    )
    return report.passed


def export_embeddings(
    model_id: str = DEFAULT_MODEL_ID,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    opset: int = DEFAULT_OPSET,
    validate_threshold: float = 0.95,
    skip_validate: bool = False,
) -> Path:
    """Full export pipeline. Returns the int8 model path."""
    _require_export_deps()

    version_dir = Path(output_dir) / "1"
    fp32_path = export_fp32(model_id, version_dir, opset=opset)
    int8_path = quantize_int8(fp32_path, version_dir)

    if not skip_validate:
        if not run_validation(fp32_path, int8_path, version_dir, validate_threshold):
            raise SystemExit(
                f"int8 validation failed (mean cosine <= {validate_threshold}); "
                f"artifacts kept at {version_dir} for inspection"
            )

    logger.info(
        "export_complete",
        fp32=str(fp32_path),
        int8=str(int8_path),
        tokenizer_dir=str(version_dir),
    )
    return int8_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export multilingual-e5 to ONNX int8 (CPU fallback)")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID, help="HuggingFace model id")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Model repository directory (version dir '<output-dir>/1' is created)",
    )
    parser.add_argument("--opset", type=int, default=DEFAULT_OPSET, help="ONNX opset version")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.95,
        help="Min mean cosine similarity (int8 vs FP32) for validation",
    )
    parser.add_argument("--skip-validate", action="store_true", help="Skip the cosine-similarity validation")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    export_embeddings(
        model_id=args.model_id,
        output_dir=args.output_dir,
        opset=args.opset,
        validate_threshold=args.threshold,
        skip_validate=args.skip_validate,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
