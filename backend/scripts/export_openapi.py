"""Export the FastAPI OpenAPI document to docs/openapi.yaml (EP-41.6).

Usage (from ``backend/``):

    uv run python scripts/export_openapi.py

The script imports the ASGI app, renders its OpenAPI schema and writes a
YAML document to ``<repo-root>/docs/openapi.yaml``. Run it whenever
endpoints, schemas, or OpenAPI metadata change.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "openapi.yaml"


def build_schema() -> dict:
    """Render the OpenAPI schema for the scanner app."""
    sys.path.insert(0, str(BACKEND_DIR))
    from app.main import app

    schema: dict = app.openapi()
    version = str(schema.get("openapi", ""))
    if not version.startswith("3.1"):
        raise SystemExit(f"expected OpenAPI 3.1.x, got {version!r}")
    return schema


def export(output: Path = DEFAULT_OUTPUT) -> Path:
    schema = build_schema()
    output.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Auto-generated from the FastAPI app — do not edit by hand.\n"
        "# Regenerate with: cd backend && uv run python scripts/export_openapi.py\n"
    )
    body = yaml.safe_dump(schema, sort_keys=False, allow_unicode=True, width=100)
    output.write_text(header + body, encoding="utf-8")
    return output


if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_OUTPUT
    path = export(target)
    print(f"OpenAPI spec written to {path}")
