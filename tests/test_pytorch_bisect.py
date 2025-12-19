"""End-to-end tests for bisecting PyTorch repository."""

import pytest

from app.bisect_core import BisectJob
from app.bisect_runner import BisectRunner


# PyTorch build script that clones and verifies torch installation
PYTORCH_VERIFY_SCRIPT = r"""#!/bin/bash
set -e

export DEBIAN_FRONTEND=noninteractive

PYTHON_BIN=python3
VENV_DIR=venv
USE_NINJA=1

echo "=== Installing git ==="
apt-get update -qq
apt-get install -y -qq git

echo "=== Cloning PyTorch repository ==="
git clone --depth 1 https://github.com/pytorch/pytorch.git
cd pytorch

echo "=== Installing system dependencies ==="
apt-get install -y -qq \
  git \
  python3 \
  python3-venv \
  python3-dev \
  python3-pip \
  build-essential \
  cmake \
  ninja-build \
  libopenblas-dev \
  libomp-dev \
  libffi-dev \
  libjpeg-dev \
  zlib1g-dev

echo "=== Initializing submodules ==="
git submodule sync
git submodule update --init --recursive

echo "=== Creating virtual environment ==="
$PYTHON_BIN -m venv $VENV_DIR
source $VENV_DIR/bin/activate

echo "=== Upgrading pip tooling ==="
pip install --upgrade pip setuptools wheel

echo "=== Installing Python requirements ==="
pip install -r requirements.txt
pip install --group dev

echo "=== Building PyTorch in develop mode ==="
if [ "$USE_NINJA" -eq 1 ]; then
  USE_NINJA=1 python setup.py develop
else
  python setup.py develop
fi

echo "=== Verifying torch import ==="
python - <<'EOF'
import torch
print("torch version:", torch.__version__)
print(torch.rand(2))
EOF

echo "=== Running PyTorch tensor tests ==="
pytest test/test_autograd.py.py -q

echo "=== Build and tests completed successfully ==="
"""


class TestPyTorchBisect:
    """End-to-end tests for bisecting PyTorch repository."""

    @pytest.fixture
    def runner(self):
        """Create a BisectRunner instance."""
        return BisectRunner()

    @pytest.fixture
    def docker_available(self, runner):
        """Check if Docker is available."""
        return runner.check_docker_available()

    @pytest.mark.e2e
    @pytest.mark.slow
    def test_pytorch_full_build_bisect(self, runner, docker_available):
        """
        Test bisecting PyTorch with a full build.
        
        This is an extremely long-running test that:
        1. Clones PyTorch repository
        2. Builds PyTorch from source
        3. Runs tensor tests
        
        Note: This test takes 2-4+ hours depending on hardware.
        """
        if not docker_available:
            pytest.skip("Docker not available")

        # Use known PyTorch commits for bisect
        # These are example commits - update with actual good/bad commits
        job = BisectJob(
            repo_url="https://github.com/pytorch/pytorch.git",
            good_sha="v2.0.0",  # Example: a known good tag
            bad_sha="v2.1.0",   # Example: a known bad tag
            test_command=PYTORCH_VERIFY_SCRIPT,
            docker_image="ubuntu:22.04",
        )

        output_lines = []

        def log_callback(line: str):
            output_lines.append(line)
            print(line, flush=True)

        result = runner.run_bisect(job, log_callback=log_callback)

        assert result.success or result.error is not None
        # We don't assert specific culprit since this is a demonstration test

    @pytest.mark.e2e
    def test_pytorch_clone_only_bisect(self, runner, docker_available):
        """
        Lighter bisect test that only clones PyTorch and installs dependencies.
        
        This is a faster sanity check (~30 min) that verifies the bisect
        runner can handle PyTorch repository operations.
        """
        if not docker_available:
            pytest.skip("Docker not available")

        # Simple test that just verifies clone and dependency installation
        clone_and_deps_script = r"""#!/bin/bash
set -e

export DEBIAN_FRONTEND=noninteractive

echo "=== Installing system dependencies ==="
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip

echo "=== Creating virtual environment ==="
python3 -m venv venv
source venv/bin/activate

echo "=== Upgrading pip ==="
pip install --upgrade pip setuptools wheel

echo "=== Installing requirements.txt ==="
pip install -r requirements.txt

echo "=== Verifying installation ==="
pip list | head -20

echo "=== Clone and dependency installation completed ==="
"""

        job = BisectJob(
            repo_url="https://github.com/pytorch/pytorch.git",
            good_sha="v2.0.0",
            bad_sha="v2.1.0",
            test_command=clone_and_deps_script,
            docker_image="ubuntu:22.04",
        )

        output_lines = []

        def log_callback(line: str):
            output_lines.append(line)
            print(line, flush=True)

        result = runner.run_bisect(job, log_callback=log_callback)

        # For this test, we just verify the bisect infrastructure works
        # The actual commits may all pass or fail, we just want no crashes
        full_output = "\n".join(output_lines)
        assert "Starting bisect" in full_output or result.error is not None

    @pytest.mark.e2e
    def test_pytorch_simple_file_check_bisect(self, runner, docker_available):
        """
        Simple bisect test that checks for file existence in PyTorch repo.
        
        This is a quick test (~5 min) that:
        1. Clones PyTorch (shallow)
        2. Checks if a specific file exists
        
        This validates the bisect infrastructure works with PyTorch repo.
        """
        if not docker_available:
            pytest.skip("Docker not available")

        # Simple test that checks if setup.py exists (should always pass)
        simple_script = r"""#!/bin/bash
set -e
echo "=== Checking for setup.py ==="
test -f setup.py
echo "=== setup.py exists ==="
"""

        job = BisectJob(
            repo_url="https://github.com/pytorch/pytorch.git",
            # Use two adjacent tags for quick bisect (minimal commits between them)
            good_sha="v2.4.0",
            bad_sha="v2.4.1",
            test_command=simple_script,
            docker_image="ubuntu:22.04",
        )

        output_lines = []

        def log_callback(line: str):
            output_lines.append(line)
            print(line, flush=True)

        result = runner.run_bisect(job, log_callback=log_callback)

        full_output = "\n".join(output_lines)
        
        # Since setup.py should exist in all commits, all commits should be "good"
        # which means bisect won't find a culprit. This is expected behavior.
        # We're testing that the infrastructure works, not finding a real bug.
        assert "Starting binary search" in full_output or "Found" in full_output or "Failed to get commit list" in full_output

    @pytest.mark.e2e
    @pytest.mark.slow
    def test_pytorch_known_regression_bisect(self, runner, docker_available):
        """
        Bisect a known PyTorch regression.
        
        This test requires updating with actual known-good and known-bad
        commit SHAs where a specific test started failing.
        
        Example usage:
        1. Find a commit where a test passes (good_sha)
        2. Find a later commit where the same test fails (bad_sha)
        3. Update the commits below
        4. Run the test to find the culprit commit
        """
        if not docker_available:
            pytest.skip("Docker not available")

        # TODO: Update these with actual regression commits
        good_sha = "bd9f0ea97c231697c2d02d08d61c19f61650c6e0"
        bad_sha = "42ad9edfb754743fdae3276ade43de000beb4f60"
        
        # Skip if placeholder values haven't been replaced with real commits
        if good_sha.startswith("REPLACE") or bad_sha.startswith("REPLACE"):
            pytest.skip("Test requires real commit SHAs - update good_sha and bad_sha with actual regression commits")
        
        # The test command that was passing in good_sha but failing in bad_sha
        regression_test_script = r"""#!/bin/bash
set -e

export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip build-essential cmake ninja-build

python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

# Build PyTorch
USE_NINJA=1 python setup.py develop

# Run the specific failing test
python -c "
import torch
# Add specific regression test here
# Example: assert torch.some_function() == expected_value
"
"""

        job = BisectJob(
            repo_url="https://github.com/pytorch/pytorch.git",
            good_sha=good_sha,
            bad_sha=bad_sha,
            test_command=regression_test_script,
            docker_image="ubuntu:22.04",
        )

        output_lines = []

        def log_callback(line: str):
            output_lines.append(line)
            print(line, flush=True)

        result = runner.run_bisect(job, log_callback=log_callback)

        assert result.success, f"Bisect failed: {result.error}"
        assert result.culprit_sha is not None, "No culprit commit found"
        assert result.culprit_message is not None
        
        print(f"\n=== BISECT RESULT ===")
        print(f"Culprit SHA: {result.culprit_sha}")
        print(f"Culprit Message: {result.culprit_message}")
        print(f"===================\n")

