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


@pytest.fixture(scope="session")
def opa_server(opa_binary: str) -> Generator[str, None, None]:
    """Start OPA server in a subprocess."""
    # Find free port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        port = s.getsockname()[1]
    
    # Start OPA server
    cmd = [opa_binary, "run", "--server", "--addr", f"localhost:{port}"]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
    
    yield f"http://localhost:{port}"
    
    # Clean up
    process.terminate()
    process.wait()


@pytest.fixture(scope="session")
def opa_client(opa_server: str, opa_binary: str) -> OPAClient:
    """OPA client with all policies loaded."""
    client = OPAClient(opa_server)
    
    # Load all .rego files from policies directory
    policies_dir = Path(__file__).parent.parent.parent.parent / "policies"
    if not policies_dir.exists():
        pytest.fail("policies directory not found")
    
    for rego_file in policies_dir.rglob("*.rego"):
        rel_path = rego_file.relative_to(policies_dir)
        policy_id = str(rel_path.with_suffix("")).replace("\\", "/")
        
        try:
            with open(rego_file, "r", encoding="utf-8") as f:
                policy_content = f.read()
            
            client.save_policy(policy_id, policy_content)
        except Exception as e:
            pytest.fail(f"Failed to load policy {policy_id}: {e}")
    
    return client


@pytest.fixture
def evaluate(opa_client: OPAClient):
    """Helper to evaluate policy with input."""
    def _evaluate(package: str, input_data: dict) -> dict:
        return opa_client.check_policy(package, input_data)
    return _evaluate