#!/usr/bin/env python3
"""
Export trained classifier to ONNX (FP32 optimized + int8 dynamic).

Pipeline (Prompt 17 / EP-10):
  1. Load the trained model from ml-models/classifier/v1
  2. Export to ONNX using optimum-cli export onnx --task text-classification
  3. Optimize the FP32 graph for CPU inference (best-effort)
  4. Normalize the graph contract: inputs input_ids/attention_mask (INT64),
     single output ``logits`` (FP32, [batch, 4]) — matches
     ml_classifier.py inference contract.
  5. Dynamic int8 quantization using optimum-cli onnxruntime quantize --avx2
  6. Validate: F1 degradation (FP32 vs int8 on test set) < 2%

Artifacts (versioned in ml-models/classifier/v1/):
  ml-models/classifier/v1/model.onnx          # FP32 (optimized)
  ml-models/classifier/v1/model_int8.onnx     # int8 (CPU fallback)

Usage:
    pip install -e "backend[export]"
    python -m app.ml.export_classifier_onnx [--model-dir ml-models/classifier/v1]

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

DEFAULT_MODEL_DIR = "ml-models/classifier/v1"
DEFAULT_OPSET = 17
DEFAULT_MAX_DEGRADATION = 0.02

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


def _get_optimum_cli_path() -> str:
    """Get path to optimum-cli executable, prefer installed CLI."""
    cli_path = shutil.which("optimum-cli")
    if cli_path:
        return cli_path
    # Fallback: try running as module
    return sys.executable


def _run_optimum_cli(args: list[str], cwd: Optional[Path] = None) -> None:
    """Run optimum-cli command with subprocess, log output."""
    import subprocess
    
    cli_path = _get_optimum_cli_path()
    full_cmd = [cli_path] + args
    
    logger.info("running_optimum_cli", command=full_cmd, cwd=str(cwd) if cwd else None)
    
    try:
        result = subprocess.run(
            full_cmd,
            check=True,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
        )
        if result.stdout:
            logger.debug("optimum_stdout", stdout=result.stdout)
        if result.stderr:
            logger.debug("optimum_stderr", stderr=result.stderr)
    except subprocess.CalledProcessError as e:
        logger.error("optimum_cli_failed", command=full_cmd, returncode=e.returncode, stdout=e.stdout, stderr=e.stderr)
        raise SystemExit(f"optimum-cli failed: {e.stderr}")


def normalize_classifier_contract(model_path: Path) -> None:
    """Enforce the classifier I/O contract on an exported ONNX graph.

    - First graph output is renamed to ``logits`` when needed.
    - Extra graph outputs (e.g. hidden_states) are dropped so the graph
      matches ml_classifier.py exactly.
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
    if outputs[0].name != "logits":
        logger.info("renaming_output", old=outputs[0].name, new="logits")
        outputs[0].name = "logits"
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
    full_outputs[0].name = "logits"
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


def export_fp32(model_dir: Path, opset: int = DEFAULT_OPSET) -> Path:
    """Export the trained model to an optimized FP32 ONNX file in ``model_dir``.

    Returns the path to model.onnx. Tokenizer/config files are already present
    in model_dir from training. The ORT graph-optimization promotion is verified
    with a real session load and rolled back on failure.
    """
    import onnx
    from optimum.onnxruntime import ORTOptimizer
    from optimum.onnxruntime.configuration import OptimizationConfig

    logger.info("exporting_fp32", model_dir=str(model_dir), opset=opset)

    # Create temporary directory for export
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        
        # Export using optimum-cli
        _run_optimum_cli([
            "export", "onnx",
            "--model", str(model_dir),
            "--task", "text-classification",
            "--opset", str(opset),
            str(tmp)
        ], cwd=model_dir.parent)

        # Check if export produced model.onnx
        fp32_path = tmp / FP32_FILENAME
        if not fp32_path.exists():
            raise RuntimeError(f"optimum export did not produce {fp32_path}")

        # Best-effort graph optimization — keep the unoptimized file on failure.
        backups: dict[Path, Path] = {}
        try:
            logger.info("optimizing_fp32", level=99)
            optimizer = ORTOptimizer.from_pretrained(str(tmp))
            opt_dir = tmp / "optimized"
            optimizer.optimize(
                optimization_config=OptimizationConfig(optimization_level=99),
                save_dir=str(opt_dir),
                file_suffix="optimized",
            )
            optimized = opt_dir / "model_optimized.onnx"
            if optimized.exists():
                # Snapshot the original export so we can roll back if the
                # promoted optimized graph turns out unloadable.
                for f in (fp32_path, tmp / EXTERNAL_DATA_FILENAME):
                    if f.exists():
                        backup = f.with_name(f.name + ".backup")
                        shutil.copyfile(f, backup)
                        backups[f] = backup

                # Move optimized files to main export dir
                for stale in (fp32_path, tmp / EXTERNAL_DATA_FILENAME):
                    if stale.exists():
                        stale.unlink()
                shutil.move(str(optimized), str(fp32_path))
                
                # Handle external data
                data_candidates = [
                    opt_dir / "model_optimized.onnx.data",
                    opt_dir / "model_optimized.onnx_data",
                ]
                opt_data = next((c for c in data_candidates if c.exists()), None)
                if opt_data is not None:
                    target_data = tmp / "model_optimized.onnx.data"
                    shutil.move(str(opt_data), str(target_data))
                    # Update location refs
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
                        target_data.rename(tmp / EXTERNAL_DATA_FILENAME)

                # Verify the optimized model loads
                _smoke_test_model(fp32_path)
                logger.info("fp32_optimized", path=str(fp32_path))
        except Exception as exc:  # noqa: BLE001 — optimization is optional
            logger.warning("fp32_optimize_failed", error=str(exc), fallback="unoptimized")
            for stray_name in ("model_optimized.onnx.data", "model_optimized.onnx_data"):
                stray = tmp / stray_name
                if stray.exists():
                    stray.unlink()
            for original, backup in backups.items():
                shutil.copyfile(backup, original)
        finally:
            for backup in backups.values():
                if backup.exists():
                    backup.unlink()

        # Move model to final location
        final_fp32_path = model_dir / FP32_FILENAME
        if final_fp32_path.exists():
            final_fp32_path.unlink()
        shutil.move(str(fp32_path), str(final_fp32_path))
        
        # Move external data if present
        external_data = tmp / EXTERNAL_DATA_FILENAME
        if external_data.exists():
            final_external = model_dir / EXTERNAL_DATA_FILENAME
            if final_external.exists():
                final_external.unlink()
            shutil.move(str(external_data), str(final_external))

    # Normalize contract
    normalize_classifier_contract(final_fp32_path)
    return final_fp32_path


def quantize_int8(fp32_path: Path, model_dir: Path) -> Path:
    """Dynamic int8 quantization of the FP32 model using optimum-cli.

    Uses ``optimum-cli onnxruntime quantize --avx2``.
    Returns the path to model_int8.onnx inside ``model_dir``.
    """
    logger.info("quantizing_int8", source=str(fp32_path))

    int8_path = model_dir / INT8_FILENAME
    
    # Create temporary directory for quantization
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        
        # Quantize using optimum-cli
        _run_optimum_cli([
            "onnxruntime", "quantize",
            "--onnx_model", str(fp32_path.parent),  # Directory containing model.onnx
            "-o", str(tmp),
            "--avx2"
        ], cwd=model_dir.parent)
        
        # Find the quantized model (usually model_quantized.onnx)
        quantized_files = list(tmp.glob("*.onnx"))
        if not quantized_files:
            raise RuntimeError(f"quantizer produced no ONNX file in {tmp}")
        
        quantized_file = quantized_files[0]  # Take the first .onnx file
        
        # Move to final location
        if int8_path.exists():
            int8_path.unlink()
        shutil.move(str(quantized_file), str(int8_path))
        
        # Handle external data if present
        external_data_candidates = [
            tmp / "model_quantized.onnx.data",
            tmp / "model_quantized.onnx_data",
        ]
        external_data = next((c for c in external_data_candidates if c.exists()), None)
        if external_data is not None:
            final_external = model_dir / "model_quantized.onnx.data"
            if final_external.exists():
                final_external.unlink()
            shutil.move(str(external_data), str(final_external))
            
            # Update location refs in the model
            proto = onnx.load(str(int8_path), load_external_data=False)
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
                            entry.value = "model_quantized.onnx.data"
            onnx.save(proto, str(int8_path))
            if (model_dir / "model_quantized.onnx.data").exists():
                (model_dir / "model_quantized.onnx.data").rename(model_dir / EXTERNAL_DATA_FILENAME)

    # Normalize contract
    normalize_classifier_contract(int8_path)
    logger.info("int8_ready", path=str(int8_path), size_mb=round(int8_path.stat().st_size / 1e6, 1))
    return int8_path


def run_validation(fp32_path: Path, int8_path: Path, model_dir: Path, threshold: float) -> bool:
    """Run the FP32-vs-int8 F1 degradation check. Returns True on pass."""
    from app.ml.validate_onnx_classifier import validate

    # Use test.jsonl from model directory
    test_set_path = model_dir / "test.jsonl"
    if not test_set_path.exists():
        logger.warning("test_set_missing", path=str(test_set_path), fallback="validation skipped")
        return True  # Skip validation if test set not available
    
    report = validate(
        fp32_path, 
        int8_path, 
        model_dir, 
        test_set_path,
        max_degradation=threshold
    )
    logger.info(
        "validation_report",
        num_examples=report.num_examples,
        baseline_f1=round(report.baseline_f1, 4),
        int8_f1=round(report.int8_f1, 4),
        degradation=round(report.degradation, 4),
        threshold=report.max_degradation,
        passed=report.passed,
    )
    return report.passed


def export_classifier(
    model_dir: str | Path = DEFAULT_MODEL_DIR,
    opset: int = DEFAULT_OPSET,
    validate_threshold: float = DEFAULT_MAX_DEGRADATION,
    skip_validate: bool = False,
) -> Path:
    """Full export pipeline. Returns the int8 model path."""
    model_dir = Path(model_dir)
    
    # Check if trained model exists
    if not (model_dir / "config.json").exists():
        raise SystemExit(
            f"Trained model config not found at {model_dir}/config.json\n"
            "Run training first: python -m app.ml.train_classifier"
        )
    if not (model_dir / "model.safetensors").exists() and not (model_dir / "pytorch_model.bin").exists():
        raise SystemExit(
            f"Trained model weights not found at {model_dir}/model.safetensors or {model_dir}/pytorch_model.bin\n"
            "Run training first: python -m app.ml.train_classifier"
        )
    
    # Check export dependencies after verifying model exists
    _require_export_deps()
    
    logger.info("export_start", model_dir=str(model_dir))
    
    # Export FP32
    fp32_path = export_fp32(model_dir, opset=opset)
    
    # Quantize to int8
    int8_path = quantize_int8(fp32_path, model_dir)
    
    # Validate
    if not skip_validate:
        if not run_validation(fp32_path, int8_path, model_dir, validate_threshold):
            raise SystemExit(
                f"int8 validation failed (F1 degradation >= {validate_threshold}); "
                f"artifacts kept at {model_dir} for inspection"
            )
    
    logger.info(
        "export_complete",
        fp32=str(fp32_path),
        int8=str(int8_path),
        model_dir=str(model_dir),
    )
    return int8_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export trained classifier to ONNX int8 (CPU fallback)"
    )
    parser.add_argument(
        "--model-dir",
        default=DEFAULT_MODEL_DIR,
        help="Model directory (contains config.json, model.safetensors, tokenizer.json, test.jsonl)"
    )
    parser.add_argument(
        "--opset",
        type=int,
        default=DEFAULT_OPSET,
        help="ONNX opset version"
    )
    parser.add_argument(
        "--max-degradation",
        type=float,
        default=DEFAULT_MAX_DEGRADATION,
        help="Maximum allowed F1 degradation (default 0.02)"
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip the F1 degradation validation"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    export_classifier(
        model_dir=args.model_dir,
        opset=args.opset,
        validate_threshold=args.max_degradation,
        skip_validate=args.skip_validate,
    )
    return 0


if __name__ == "__main__":
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer()
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        context_class=dict,
        cache_logger_on_first_use=True,
    )
    raise SystemExit(main())