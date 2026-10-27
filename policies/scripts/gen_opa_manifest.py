"""Generate policies/manifest.json for versioned OPA bundle deployment (EP-12 Prompt 28).

The manifest describes every bundle shipped in a policy release:

    {
      "version": "1.0.0",
      "bundles": [
        {"id": "global", "path": "global/", "sha256": "..."},
        {"id": "tenants_default", "path": "tenants/default/", "sha256": "..."}
      ],
      "created_at": "2026-09-27T10:00:00Z",
      "git_commit": "abc123..."
    }

Bundle ids are derived from the directory that holds the .rego files
("tenants/default" -> "tenants_default"). The sha256 is computed over the
sorted (relative path, content) pairs of that directory so the manifest is
reproducible for a given set of policies.

Usage:
    python backend/scripts/gen_opa_manifest.py \
        --policies-dir policies \
        --output policies/manifest.json \
        --version 1.0.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _sha256_of_dir(directory: Path) -> str:
    """Deterministic hash of all .rego files in *directory* (sorted by relpath)."""
    files = sorted(directory.rglob("*.rego"))
    digest = hashlib.sha256()
    for file in files:
        rel = file.relative_to(directory).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def discover_bundles(policies_dir: Path) -> list[dict]:
    """Discover policy bundles -> [{id, path, sha256}].

    Layout convention (see ALPHA-PROMPTS Prompt 28):
      - policies/global/*.rego      -> one bundle  {id: "global", path: "global/"}
      - policies/tenants/<t>.rego   -> one bundle per tenant file
                                       {id: "tenants_<t>", path: "tenants/<t>.rego"}
      - policies/tenants/<t>/*.rego -> one bundle per tenant dir
                                       {id: "tenants_<t>", path: "tenants/<t>/"}
    """
    bundles: list[dict] = []

    def _visible(path: Path) -> bool:
        return not any(part.startswith(".") for part in path.parts)

    for top in sorted(p for p in policies_dir.iterdir() if p.is_dir() and _visible(p)):
        if top.name == "bundles":
            continue  # built artifacts, never a source bundle
        top_rel = top.relative_to(policies_dir).as_posix()

        if top.name == "tenants":
            for tenant_file in sorted(top.glob("*.rego")):
                stem = tenant_file.stem
                bundles.append(
                    {
                        "id": f"tenants_{stem}",
                        "path": f"{top_rel}/{tenant_file.name}",
                        "sha256": hashlib.sha256(tenant_file.read_bytes()).hexdigest(),
                    }
                )
            for tenant_dir in sorted(p for p in top.iterdir() if p.is_dir() and _visible(p)):
                bundles.append(
                    {
                        "id": f"tenants_{tenant_dir.name}",
                        "path": f"{top_rel}/{tenant_dir.name}/",
                        "sha256": _sha256_of_dir(tenant_dir),
                    }
                )
        else:
            if not any(top.rglob("*.rego")):
                continue
            bundles.append(
                {
                    "id": top_rel.replace("/", "_"),
                    "path": f"{top_rel}/",
                    "sha256": _sha256_of_dir(top),
                }
            )
    return bundles


def git_commit_of(repo_dir: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def build_manifest(
    policies_dir: Path,
    version: str,
    git_commit: str | None = None,
    created_at: datetime | None = None,
) -> dict:
    policies_dir = policies_dir.resolve()
    if not policies_dir.is_dir():
        raise FileNotFoundError(f"policies dir not found: {policies_dir}")

    bundles = discover_bundles(policies_dir)
    if not bundles:
        raise RuntimeError(f"no .rego files found under {policies_dir}")

    if git_commit is None:
        git_commit = git_commit_of(policies_dir.parent)
    if created_at is None:
        created_at = datetime.now(timezone.utc)

    return {
        "version": version,
        "bundles": bundles,
        "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_commit": git_commit,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate OPA bundle manifest.json")
    parser.add_argument("--policies-dir", default="policies", type=Path)
    parser.add_argument("--output", default=None, type=Path,
                        help="Output path (default: <policies-dir>/manifest.json)")
    parser.add_argument("--version", required=True, help="Semantic version (e.g. 1.0.0)")
    parser.add_argument("--git-commit", default=None, help="Override git commit (default: HEAD)")
    args = parser.parse_args(argv)

    output = args.output or (args.policies_dir / "manifest.json")
    manifest = build_manifest(
        policies_dir=args.policies_dir,
        version=args.version,
        git_commit=args.git_commit,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output} (version={manifest['version']}, "
          f"bundles={[b['id'] for b in manifest['bundles']]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
