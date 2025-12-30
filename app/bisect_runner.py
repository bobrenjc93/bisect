"""Local bisect runner that runs git bisect in the same container.

This is a simplified runner that executes bisect operations directly on the filesystem,
eliminating the need for Docker-in-Docker and the associated complexity/security concerns.
"""

import logging
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional, Callable, List, Tuple

from app.config import get_settings
from app.bisect_core import BisectJob, BisectResult

logger = logging.getLogger(__name__)

# Type alias for log callback
LogCallback = Callable[[str], None]

# Re-export for backwards compatibility
__all__ = ["BisectJob", "BisectResult", "BisectRunner"]


def run_command(
    cmd: List[str],
    cwd: Optional[str] = None,
    log_callback: Optional[LogCallback] = None,
) -> Tuple[int, str, str]:
    """Run a command and return exit code, stdout, stderr."""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def run_command_streaming(
    cmd: List[str],
    cwd: Optional[str] = None,
    log_callback: Optional[LogCallback] = None,
) -> Tuple[int, str]:
    """Run a command with streaming output. Returns exit code and combined output."""
    output_lines = []
    
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    
    for line in iter(process.stdout.readline, ''):
        line = line.rstrip()
        output_lines.append(line)
        if log_callback:
            log_callback(f"   │ {line}")
    
    process.wait()
    return process.returncode, "\n".join(output_lines)


class BisectRunner:
    """Runs git bisect operations directly on the local filesystem."""

    def __init__(self):
        self.settings = get_settings()

    def check_docker_available(self) -> bool:
        """Check if git is available (renamed for backwards compatibility with health checks)."""
        try:
            result = subprocess.run(["git", "--version"], capture_output=True)
            return result.returncode == 0
        except Exception:
            return False

    def run_bisect(
        self,
        job: BisectJob,
        log_callback: Optional[LogCallback] = None,
    ) -> BisectResult:
        """
        Run a bisect job locally using git bisect run.

        This approach:
        1. Clones the repository to a temporary directory
        2. Creates build_and_test.sh with the user's test command
        3. Uses git bisect run ./build_and_test.sh to find the first bad commit
        4. Cleans up the temporary directory when done

        Args:
            job: The bisect job configuration
            log_callback: Optional callback function that receives log lines in real-time
        """
        logger.info(f"Starting bisect: good={job.good_sha[:7]}, bad={job.bad_sha[:7]}")

        if log_callback:
            log_callback(f"🚀 Starting bisect job...")
            log_callback(f"🔀 Good commit: {job.good_sha[:7]}")
            log_callback(f"🔀 Bad commit: {job.bad_sha[:7]}")

        # Create a unique temp directory for this bisect job
        work_dir = Path(tempfile.mkdtemp(prefix=f"bisect-{uuid.uuid4().hex[:8]}-"))
        repo_dir = work_dir / "repo"
        
        try:
            # Clone the repository
            if log_callback:
                log_callback(f"📋 Cloning repository...")

            clone_success = self._clone_repo(job, repo_dir, log_callback)
            if not clone_success:
                return BisectResult(
                    success=False,
                    error="Failed to clone repository",
                )

            if log_callback:
                log_callback(f"✅ Repository cloned successfully")

            # Run git bisect
            return self._run_git_bisect(job, repo_dir, log_callback)

        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            logger.error("=" * 50)
            logger.error(f"❌ UNEXPECTED ERROR DURING BISECT")
            logger.error("=" * 50)
            logger.error(f"  Type: {error_type}")
            logger.error(f"  Message: {error_msg}")
            logger.exception("  Traceback:")
            logger.error("-" * 50)
            logger.error("📋 TROUBLESHOOTING:")
            logger.error("  • Verify the repository is accessible")
            logger.error("  • Ensure good_sha and bad_sha are valid commits")
            logger.error("  • Check that test_command runs correctly")
            logger.error("=" * 50)
            if log_callback:
                log_callback(f"❌ Unexpected error ({error_type}): {e}")
            return BisectResult(
                success=False,
                error=f"Unexpected error ({error_type}): {e}",
            )

        finally:
            # Always clean up the temp directory
            self._cleanup_dir(work_dir)

    def _cleanup_dir(self, work_dir: Path) -> None:
        """Clean up a temporary directory."""
        try:
            if work_dir.exists():
                shutil.rmtree(work_dir)
                logger.info(f"Cleaned up work directory: {work_dir}")
        except Exception as e:
            logger.warning(f"Failed to clean up directory {work_dir}: {e}")

    def _clone_repo(
        self,
        job: BisectJob,
        repo_dir: Path,
        log_callback: Optional[LogCallback] = None,
    ) -> bool:
        """Clone the repository."""
        try:
            exit_code, output = run_command_streaming(
                ["git", "clone", "--progress", job.repo_url, str(repo_dir)],
                log_callback=log_callback,
            )

            if exit_code != 0:
                logger.error(f"Failed to clone repository: {output}")
                logger.error("-" * 50)
                logger.error("📋 POSSIBLE CAUSES:")
                if "authentication" in output.lower() or "permission" in output.lower():
                    logger.error("  → Repository access denied")
                    logger.error("    • Check GitHub App installation permissions")
                    logger.error("    • Ensure the App has access to the repository")
                else:
                    logger.error(f"  → Git error: {output}")
                logger.error("-" * 50)
                if log_callback:
                    log_callback(f"❌ Failed to clone repository")
                return False

            return True

        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"Exception cloning repository ({error_type}): {e}")
            if log_callback:
                log_callback(f"❌ Failed to clone repository ({error_type}): {e}")
            return False

    def _run_git_bisect(
        self,
        job: BisectJob,
        repo_dir: Path,
        log_callback: Optional[LogCallback] = None,
    ) -> BisectResult:
        """Run git bisect using git bisect run with build_and_test.sh."""
        output_lines = []

        def log(msg: str):
            output_lines.append(msg)
            if log_callback:
                log_callback(msg)

        log(f"")
        log(f"▶️ Starting git bisect run...")

        try:
            # Configure git
            subprocess.run(
                ["git", "config", "user.email", "bisect-bot@example.com"],
                cwd=str(repo_dir),
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Bisect Bot"],
                cwd=str(repo_dir),
                capture_output=True,
            )

            # Create build_and_test.sh script
            test_script_path = repo_dir / "build_and_test.sh"
            test_script_path.write_text(f"""#!/bin/bash
# Auto-generated build and test script for git bisect
# Exit code 0 = good commit (test passes)
# Exit code 1-124, 126-127 = bad commit (test fails)
# Exit code 125 = skip this commit (untestable)
set -e
{job.test_command}
""")
            test_script_path.chmod(0o755)

            log(f"📝 Created build_and_test.sh with test command")

            # Start bisect
            exit_code, stdout, stderr = run_command(
                ["git", "bisect", "start", job.bad_sha, job.good_sha],
                cwd=str(repo_dir),
            )
            if exit_code != 0:
                error_msg = f"Failed to start bisect: {stderr}"
                log(f"❌ {error_msg}")
                return BisectResult(
                    success=False,
                    error=error_msg,
                    output="\n".join(output_lines),
                )

            log(f"🔍 Running bisect...")
            log(f"")

            # Run bisect with streaming output
            exit_code, bisect_output = run_command_streaming(
                ["git", "bisect", "run", "./build_and_test.sh"],
                cwd=str(repo_dir),
                log_callback=log_callback,
            )

            # Parse the output to find the culprit commit
            culprit_sha = None
            for line in bisect_output.split("\n"):
                if "is the first bad commit" in line:
                    parts = line.split()
                    if parts:
                        culprit_sha = parts[0]
                        break

            if culprit_sha:
                # Get the commit message
                _, msg_stdout, _ = run_command(
                    ["git", "log", "-1", "--pretty=%s", culprit_sha],
                    cwd=str(repo_dir),
                )
                culprit_message = msg_stdout.strip()

                log(f"")
                log(f"🎯 Found first bad commit: {culprit_sha[:7]}")
                log(f"")
                log(f"=== BISECT RESULT ===")
                log(f"SUCCESS: Found culprit commit")
                log(f"SHA: {culprit_sha}")
                log(f"MESSAGE: {culprit_message}")
                log(f"=== END RESULT ===")

                # Clean up bisect state
                run_command(["git", "bisect", "reset"], cwd=str(repo_dir))

                return BisectResult(
                    success=True,
                    culprit_sha=culprit_sha,
                    culprit_message=culprit_message,
                    output="\n".join(output_lines),
                )
            else:
                # Bisect didn't find a culprit
                error_msg = "Bisect did not find a culprit commit"
                if exit_code != 0:
                    error_msg = f"Bisect failed with exit code {exit_code}"

                log(f"")
                log(f"❌ {error_msg}")

                # Clean up bisect state
                run_command(["git", "bisect", "reset"], cwd=str(repo_dir))

                return BisectResult(
                    success=False,
                    error=error_msg,
                    output="\n".join(output_lines),
                )

        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"Exception during bisect ({error_type}): {e}")
            if log_callback:
                log_callback(f"❌ Error during bisect ({error_type}): {e}")
            return BisectResult(
                success=False,
                error=f"Unexpected error ({error_type}): {e}",
                output="\n".join(output_lines),
            )
