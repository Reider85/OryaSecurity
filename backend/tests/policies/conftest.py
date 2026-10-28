"""
Fixtures for OPA policy tests.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Generator

import pytest
from opa.client import OPAClient


@pytest.fixture(scope="session")
def opa_binary() -> str:
    """Get OPA binary path, downloading if needed."""
    # Check environment variable first
    env_opa = os.environ.get("SCANNER_OPA_PATH") or os.environ.get("OPA_PATH")
    if env_opa and Path(env_opa).exists():
        return env_opa
    
    # Check system PATH
    which_result = shutil.which("opa")
    if which_result:
        return which_result
    
    # Check backend/.bin directory
    bin_dir = Path(__file__).parent.parent.parent / ".bin"
    opa_filename = "opa_windows_amd64.exe" if os.name == "nt" else "opa_linux_amd64_static"
    opa_path = bin_dir / opa_filename
    
    if opa_path.exists():
        return str(opa_path)
    
    # Download if not found
    print("OPA binary not found, downloading...")
    fetch_script = Path(__file__).parent.parent.parent / "scripts" / "fetch_opa.py"
    result = subprocess.run([sys.executable, str(fetch_script)], capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(f"Failed to download OPA: {result.stderr}")
    
    return str(opa_path)


def _load_all_policies(client: OPAClient) -> None:
    """PUT every policies/*.rego into the running OPA server.

    Loading happens HERE (opa_server fixture) and not in opa_client: the
    subprocess runs with cwd=backend/ (no .rego there), so `opa run --server`
    starts empty. Any fixture that only needs the server (e.g. the app
    OPAClient in test_bundle_deployment.py) must still see real policies.
    """
    policies_dir = Path(__file__).parent.parent.parent.parent / "policies"
    if not policies_dir.exists():
        pytest.fail("policies directory not found")

    for rego_file in policies_dir.rglob("*.rego"):
        rel_path = rego_file.relative_to(policies_dir)
        policy_id = str(rel_path.with_suffix("")).replace("\\", "/")
        try:
            client.save_policy(policy_id, rego_file.read_text(encoding="utf-8"))
        except Exception as e:
            pytest.fail(f"Failed to load policy {policy_id}: {e}")


@pytest.fixture(scope="session")
def opa_server(opa_binary: str) -> Generator[str, None, None]:
    """Start OPA server in a subprocess (with all repo policies loaded)."""
    # Find free port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        port = s.getsockname()[1]
    
    # Start OPA server
    cmd = [opa_binary, "run", "--server", "--addr", f"localhost:{port}"]
    process = subprocess.Popen(
        cmd,
        # DEVNULL, not PIPE: OPA logs every request to stderr and nobody drains
        # the pipe — once the ~4KB Windows pipe buffer fills, the server blocks
        # on write and stops responding (client hangs for its 15000s timeout).
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    )
    
    # Wait for server to be ready
    client = OPAClient(f"http://localhost:{port}")
    timeout = time.time() + 30  # 30 second timeout
    while time.time() < timeout:
        try:
            client.check_health()
            break
        except Exception:
            time.sleep(0.1)
    else:
        # Clean up and fail
        process.terminate()
        process.wait()
        pytest.fail("OPA server did not start in time")

    _load_all_policies(client)

    yield f"http://localhost:{port}"
    
    # Clean up
    process.terminate()
    process.wait()


@pytest.fixture(scope="session")
def opa_client(opa_server: str, opa_binary: str) -> OPAClient:
    """OPA client bound to the session server (policies pre-loaded by opa_server)."""
    return OPAClient(opa_server)


@pytest.fixture
def evaluate(opa_client: OPAClient):
    """Helper to evaluate policy with input."""
    def _evaluate(package: str, input_data: dict) -> dict:
        return opa_client.check_policy(package, input_data)
    return _evaluate