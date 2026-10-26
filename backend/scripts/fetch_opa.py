#!/usr/bin/env python3
"""
Download OPA binary for policy evaluation.
"""

import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

OPA_VERSION = "0.68.0"
BASE_URL = "https://openpolicyagent.org/downloads"
BIN_DIR = Path(__file__).parent.parent / ".bin"

def get_opa_filename():
    """Get OPA binary filename for current platform."""
    system = platform.system().lower()
    arch = platform.machine().lower()
    
    # Normalize architecture names
    if arch in ("amd64", "x86_64"):
        arch = "amd64"
    elif arch in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = "amd64"  # fallback
    
    if system == "windows":
        return f"opa_windows_{arch}.exe"
    elif system == "darwin":
        return f"opa_darwin_{arch}"
    else:  # linux
        return f"opa_linux_{arch}_static"

def get_opa_url():
    """Get OPA binary download URL."""
    filename = get_opa_filename()
    return f"{BASE_URL}/v{OPA_VERSION}/{filename}"

def download_opa():
    """Download OPA binary."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    opa_path = BIN_DIR / get_opa_filename()
    
    if opa_path.exists():
        print(f"OPA binary already exists at: {opa_path}")
        return str(opa_path)
    
    url = get_opa_url()
    print(f"Downloading OPA v{OPA_VERSION} from: {url}")
    
    try:
        urllib.request.urlretrieve(url, opa_path)
        if platform.system() != "windows":
            opa_path.chmod(0o755)  # Make executable on Unix
        print(f"Downloaded to: {opa_path}")
        return str(opa_path)
    except Exception as e:
        print(f"Error downloading OPA: {e}")
        sys.exit(1)

def verify_opa(opa_path):
    """Verify OPA binary works."""
    try:
        result = subprocess.run(
            [opa_path, "version"],
            capture_output=True,
            text=True,
            check=True
        )
        print(f"OPA version: {result.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"OPA verification failed: {e}")
        return False

def main():
    """Main entry point."""
    if len(sys.argv) > 1 and sys.argv[1] == "--path":
        # Just return path if already exists
        opa_path = BIN_DIR / get_opa_filename()
        if opa_path.exists():
            print(str(opa_path))
            return
    
    opa_path = download_opa()
    if not verify_opa(opa_path):
        sys.exit(1)
    
    print(f"OPA binary ready at: {opa_path}")

if __name__ == "__main__":
    main()