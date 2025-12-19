"""Core bisect logic that can run locally or in a Docker container."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple, List


@dataclass
class BisectJob:
    """Represents a bisect job to be executed."""

    repo_url: str
    good_sha: str
    bad_sha: str
    test_command: str
    docker_image: Optional[str] = None  # Custom Docker image for running bisect


@dataclass
class BisectResult:
    """Result of a bisect operation."""

    success: bool
    culprit_sha: Optional[str] = None
    culprit_message: Optional[str] = None
    output: Optional[str] = None
    error: Optional[str] = None


def run_command(cmd: List[str], cwd: Optional[str] = None) -> Tuple[int, str, str]:
    """Run a command and return exit code, stdout, stderr."""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def clone_repo(repo_url: str, target_dir: str) -> Tuple[bool, str]:
    """Clone the repository. Returns (success, error_message)."""
    code, stdout, stderr = run_command(["git", "clone", repo_url, target_dir])
    if code != 0:
        return False, f"Failed to clone: {stderr}"
    return True, ""


def run_bisect(
    repo_dir: str,
    good_sha: str,
    bad_sha: str,
    test_command: str,
) -> BisectResult:
    """Run git bisect with the given parameters."""
    result = BisectResult(success=False, output="", error=None)

    code, stdout, stderr = run_command(
        ["git", "bisect", "start", bad_sha, good_sha],
        cwd=repo_dir
    )
    if code != 0:
        result.error = f"Failed to start bisect: {stderr}"
        return result

    test_script_path = os.path.join(repo_dir, ".bisect_test.sh")
    with open(test_script_path, "w") as f:
        f.write(f"""#!/bin/bash
set -e
{test_command}
""")
    os.chmod(test_script_path, 0o755)

    code, stdout, stderr = run_command(
        ["git", "bisect", "run", test_script_path],
        cwd=repo_dir
    )
    
    result.output = stdout + stderr

    for line in (stdout + stderr).split("\n"):
        if "is the first bad commit" in line:
            parts = line.split()
            if parts:
                result.culprit_sha = parts[0]
                code, msg, _ = run_command(
                    ["git", "log", "-1", "--pretty=%s", result.culprit_sha],
                    cwd=repo_dir
                )
                if code == 0:
                    result.culprit_message = msg.strip()
                result.success = True
                break

    if not result.success and not result.error:
        result.error = "Bisect did not find a culprit commit"

    code, log_output, _ = run_command(["git", "bisect", "log"], cwd=repo_dir)
    if code == 0 and log_output:
        result.output = log_output + "\n" + (result.output or "")

    run_command(["git", "bisect", "reset"], cwd=repo_dir)

    if os.path.exists(test_script_path):
        os.remove(test_script_path)

    return result


def run_bisect_on_clone(
    repo_url: str,
    work_dir: str,
    good_sha: str,
    bad_sha: str,
    test_command: str,
) -> BisectResult:
    """Clone a repository and run bisect on it."""
    repo_dir = os.path.join(work_dir, "repo")
    
    success, error = clone_repo(repo_url, repo_dir)
    if not success:
        return BisectResult(success=False, error=error)
    
    return run_bisect(repo_dir, good_sha, bad_sha, test_command)

