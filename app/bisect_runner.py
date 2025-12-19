"""Docker-based bisect runner that spawns isolated containers for each commit test."""

import logging
import uuid
from typing import Optional, Callable, List

from dockerrun import (
    DockerRunner,
    ContainerResult,
    DockerRunError,
    ContainerError,
    ImageNotFoundError,
)

from app.config import get_settings
from app.bisect_core import BisectJob, BisectResult

logger = logging.getLogger(__name__)

# Type alias for log callback
LogCallback = Callable[[str], None]

# Re-export for backwards compatibility
__all__ = ["BisectJob", "BisectResult", "BisectRunner", "build_runner_image"]


# Shell script snippet to install git if not available
# Supports apt-get, apk, yum, dnf, zypper, pacman
GIT_INSTALL_SCRIPT = '''
# Install git if not available
if ! command -v git >/dev/null 2>&1; then
    echo "Git not found, installing..."
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq && apt-get install -y -qq git >/dev/null
    elif command -v apk >/dev/null 2>&1; then
        apk add --no-cache git >/dev/null
    elif command -v yum >/dev/null 2>&1; then
        yum install -y -q git >/dev/null
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q git >/dev/null
    elif command -v zypper >/dev/null 2>&1; then
        zypper install -y git >/dev/null
    elif command -v pacman >/dev/null 2>&1; then
        pacman -Sy --noconfirm git >/dev/null
    else
        echo "ERROR: No supported package manager found to install git"
        exit 1
    fi
    echo "Git installed successfully"
fi
'''


def _generate_test_script(
    commit_sha: str,
    test_command: str,
) -> str:
    """Generate a shell script that checkouts and runs the test (repo already cloned)."""
    return f'''#!/bin/bash
set -e

# Configure git
git config --global --add safe.directory /workspace/repo

cd /workspace/repo

echo "=== Checking out commit {commit_sha[:7]} ==="
git checkout --quiet --force {commit_sha}
git clean -fdx --quiet

echo "=== Running test command ==="
{test_command}
'''


class BisectRunner:
    """Runs git bisect operations by spawning isolated Docker containers for each test."""

    def __init__(self):
        self.settings = get_settings()
        self._docker_runner: Optional[DockerRunner] = None

    @property
    def docker_runner(self) -> DockerRunner:
        """Lazy-load Docker runner (no timeout - operations can take as long as needed)."""
        if self._docker_runner is None:
            self._docker_runner = DockerRunner(
                auto_remove=True,
            )
        return self._docker_runner

    def check_docker_available(self) -> bool:
        """Check if Docker is available and running."""
        try:
            self.docker_runner.client.ping()
            return True
        except Exception:
            return False

    def run_bisect(
        self,
        job: BisectJob,
        log_callback: Optional[LogCallback] = None,
    ) -> BisectResult:
        """
        Run a bisect job by spawning containers for each commit test.

        Args:
            job: The bisect job configuration
            log_callback: Optional callback function that receives log lines in real-time
        """
        logger.info(f"Starting bisect: good={job.good_sha[:7]}, bad={job.bad_sha[:7]}")

        # Determine which Docker image to use
        image_name = job.docker_image or self.settings.docker_runner_image

        logger.info(f"Using Docker image: {image_name}")

        if log_callback:
            log_callback(f"🚀 Starting bisect job...")
            log_callback(f"📦 Using Docker image: {image_name}")
            log_callback(f"🔀 Good commit: {job.good_sha[:7]}")
            log_callback(f"🔀 Bad commit: {job.bad_sha[:7]}")

        # Create a unique volume for this bisect job to persist the cloned repo
        volume_name = f"bisect-repo-{uuid.uuid4().hex[:12]}"
        
        try:
            # Create the volume
            self.docker_runner.client.volumes.create(name=volume_name)
            logger.info(f"Created volume: {volume_name}")

            # Clone the repository once into the volume
            if log_callback:
                log_callback(f"📋 Cloning repository and fetching commit list...")

            commits = self._clone_and_get_commits(job, image_name, volume_name, log_callback)
            if commits is None:
                return BisectResult(
                    success=False,
                    error="Failed to clone repository or get commit list",
                )

            if len(commits) == 0:
                return BisectResult(
                    success=False,
                    error="No commits found between good and bad commits",
                )

            if log_callback:
                log_callback(f"📊 Found {len(commits)} commits to bisect")

            # Run binary search bisect using the same volume
            return self._run_bisect_search(job, image_name, volume_name, commits, log_callback)

        except ImageNotFoundError as e:
            error_msg = str(e)
            logger.error("=" * 50)
            logger.error(f"❌ DOCKER IMAGE NOT FOUND")
            logger.error("=" * 50)
            logger.error(f"  Image: {image_name}")
            logger.error(f"  Error: {error_msg}")
            logger.error("-" * 50)
            logger.error("📋 HOW TO FIX:")
            logger.error("  1. Build the runner image:")
            logger.error("     docker compose up runner-build")
            logger.error("  2. Or pull/build the image manually:")
            logger.error(f"     docker pull {image_name}")
            logger.error("  3. Or specify a different image in your bisect request")
            logger.error("=" * 50)
            if log_callback:
                log_callback(f"❌ {e}")
                log_callback(f"💡 Fix: Run 'docker compose up runner-build' to build the image")
            return BisectResult(
                success=False,
                error=f"Docker image '{image_name}' not found. Run 'docker compose up runner-build' to build it.",
            )

        except DockerRunError as e:
            error_msg = str(e)
            logger.error("=" * 50)
            logger.error(f"❌ DOCKER RUN ERROR")
            logger.error("=" * 50)
            logger.error(f"  Error: {error_msg}")
            logger.error("-" * 50)
            logger.error("📋 POSSIBLE CAUSES:")
            if "permission" in error_msg.lower() or "denied" in error_msg.lower():
                logger.error("  → Permission denied accessing Docker socket")
                logger.error("    • Ensure /var/run/docker.sock is mounted")
                logger.error("    • Check user permissions for Docker access")
            elif "network" in error_msg.lower():
                logger.error("  → Network error during container execution")
                logger.error("    • Check Docker network configuration")
                logger.error("    • Ensure container can access the internet for git clone")
            else:
                logger.error("  → Check Docker daemon status: docker ps")
                logger.error("  → Verify docker-compose.yml configuration")
                logger.error("  → Check container resource limits (memory/CPU)")
            logger.error("=" * 50)
            if log_callback:
                log_callback(f"❌ Docker error: {e}")
                log_callback("💡 Check the application logs for detailed troubleshooting steps")
            return BisectResult(
                success=False,
                error=str(e),
            )

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
            logger.error("  • Check if Docker daemon is running")
            logger.error("  • Verify the repository is accessible")
            logger.error("  • Ensure good_sha and bad_sha are valid commits")
            logger.error("  • Check that test_command runs correctly in the Docker image")
            logger.error("=" * 50)
            if log_callback:
                log_callback(f"❌ Unexpected error ({error_type}): {e}")
            return BisectResult(
                success=False,
                error=f"Unexpected error ({error_type}): {e}",
            )

        finally:
            # Always clean up the volume
            self._cleanup_volume(volume_name)

    def _cleanup_volume(self, volume_name: str) -> None:
        """Clean up a Docker volume."""
        try:
            volume = self.docker_runner.client.volumes.get(volume_name)
            volume.remove(force=True)
            logger.info(f"Cleaned up volume: {volume_name}")
        except Exception as e:
            logger.warning(f"Failed to clean up volume {volume_name}: {e}")

    def _clone_and_get_commits(
        self,
        job: BisectJob,
        image_name: str,
        volume_name: str,
        log_callback: Optional[LogCallback] = None,
    ) -> Optional[List[str]]:
        """Clone the repository into the volume and get the list of commits."""
        script = f'''#!/bin/bash
set -e

{GIT_INSTALL_SCRIPT}

git config --global user.email "bisect-bot@example.com"
git config --global user.name "Bisect Bot"
git config --global --add safe.directory /workspace/repo

echo "Cloning repository..."
git clone --progress "{job.repo_url}" /workspace/repo 2>&1
cd /workspace/repo

echo "Fetching commit list..."
# Get commits from good (exclusive) to bad (inclusive), oldest first
git rev-list --ancestry-path {job.good_sha}..{job.bad_sha} | tac
'''
        try:
            # Use streaming to show clone progress
            collected_output = []

            def on_output(chunk):
                text = chunk.decode().rstrip()
                if text:
                    collected_output.append(text)
                    if log_callback:
                        for line in text.split('\n'):
                            # Don't stream the commit SHAs themselves
                            if len(line) == 40 and all(c in '0123456789abcdef' for c in line):
                                continue
                            log_callback(f"   │ {line}")

            result = self.docker_runner.run_with_callback(
                image_name,
                ["bash", "-c", script],
                on_output=on_output,
                network_mode="bridge",
                working_dir="/workspace",
                volumes={volume_name: {"bind": "/workspace", "mode": "rw"}},
            )

            if not result.success:
                stderr = result.stderr_text or "Unknown error"
                logger.error(f"Failed to clone repository or get commit list: {stderr}")
                logger.error("-" * 50)
                logger.error("📋 POSSIBLE CAUSES:")
                if "not found" in stderr.lower() or "unknown revision" in stderr.lower():
                    logger.error("  → One or both commit SHAs don't exist in the repository")
                    logger.error(f"    • good_sha: {job.good_sha}")
                    logger.error(f"    • bad_sha: {job.bad_sha}")
                    logger.error("  → Verify commits exist: git rev-parse <sha>")
                elif "authentication" in stderr.lower() or "permission" in stderr.lower():
                    logger.error("  → Repository access denied")
                    logger.error("    • Check GitHub App installation permissions")
                    logger.error("    • Ensure the App has access to the repository")
                elif "not an ancestor" in stderr.lower():
                    logger.error("  → good_sha is not an ancestor of bad_sha")
                    logger.error("    • The 'good' commit must come before 'bad' in history")
                else:
                    logger.error(f"  → Git error: {stderr}")
                logger.error("-" * 50)
                if log_callback:
                    log_callback(f"❌ Failed to clone or get commit list: {stderr}")
                    log_callback("💡 Verify both commit SHAs exist and good_sha comes before bad_sha")
                return None

            # Filter to only include valid 40-character hex strings (git SHAs)
            # This filters out log messages like "Cloning repository..." that may be in stdout
            commits = [
                line.strip()
                for line in result.stdout_text.strip().split("\n")
                if line.strip() and len(line.strip()) == 40 and all(c in '0123456789abcdef' for c in line.strip())
            ]

            return commits

        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"Exception cloning repository ({error_type}): {e}")
            logger.error("-" * 50)
            logger.error("📋 TROUBLESHOOTING:")
            logger.error(f"  • Repository URL: {job.repo_url}")
            logger.error(f"  • Good SHA: {job.good_sha}")
            logger.error(f"  • Bad SHA: {job.bad_sha}")
            logger.error("  → Check if repository is accessible")
            logger.error("  → Verify GitHub App permissions")
            logger.error("-" * 50)
            if log_callback:
                log_callback(f"❌ Failed to clone repository ({error_type}): {e}")
            return None

    def _run_bisect_search(
        self,
        job: BisectJob,
        image_name: str,
        volume_name: str,
        commits: List[str],
        log_callback: Optional[LogCallback] = None,
    ) -> BisectResult:
        """Run binary search to find the first bad commit."""
        # commits is ordered from good side to bad side
        # commits[0] is the first commit after good
        # commits[-1] is the bad commit

        output_lines = []

        def log(msg: str):
            output_lines.append(msg)
            if log_callback:
                log_callback(msg)

        log(f"")
        log(f"▶️ Starting binary search over {len(commits)} commits...")

        # Binary search: find the first bad commit
        # We know: good_sha is good, bad_sha is bad
        # commits are ordered from first-after-good to bad

        left = 0  # First possible bad commit index
        right = len(commits) - 1  # Last possible bad commit index

        step = 0
        while left < right:
            step += 1
            mid = (left + right) // 2
            commit = commits[mid]

            remaining = right - left + 1
            log(f"")
            log(f"🔍 Step {step}: Testing commit {commit[:7]} ({remaining} commits remaining)")

            is_bad = self._test_commit(job, image_name, volume_name, commit, log_callback)

            if is_bad:
                log(f"   ❌ Commit {commit[:7]} is BAD")
                right = mid  # The first bad commit is at mid or before
            else:
                log(f"   ✅ Commit {commit[:7]} is GOOD")
                left = mid + 1  # The first bad commit is after mid

        # left == right, this is the first bad commit
        culprit_sha = commits[left]
        log(f"")
        log(f"🎯 Found first bad commit: {culprit_sha[:7]}")

        # Get the commit message (repo is already cloned in the volume)
        culprit_message = self._get_commit_message(job, image_name, volume_name, culprit_sha)

        log(f"")
        log(f"=== BISECT RESULT ===")
        log(f"SUCCESS: Found culprit commit")
        log(f"SHA: {culprit_sha}")
        log(f"MESSAGE: {culprit_message}")
        log(f"=== END RESULT ===")

        return BisectResult(
            success=True,
            culprit_sha=culprit_sha,
            culprit_message=culprit_message,
            output="\n".join(output_lines),
        )

    def _test_commit(
        self,
        job: BisectJob,
        image_name: str,
        volume_name: str,
        commit_sha: str,
        log_callback: Optional[LogCallback] = None,
    ) -> bool:
        """Test a single commit. Returns True if the commit is bad (test fails)."""
        script = _generate_test_script(commit_sha, job.test_command)

        try:
            # Use run_with_callback for streaming output
            collected_output = []

            def on_output(chunk):
                text = chunk.decode().rstrip()
                if text:
                    collected_output.append(text)
                    if log_callback:
                        # Indent container output
                        for line in text.split('\n'):
                            log_callback(f"   │ {line}")

            result = self.docker_runner.run_with_callback(
                image_name,
                ["bash", "-c", script],
                on_output=on_output,
                network_mode="bridge",
                working_dir="/workspace",
                volumes={volume_name: {"bind": "/workspace", "mode": "rw"}},
            )

            # Test passes (exit 0) = commit is good
            # Test fails (exit != 0) = commit is bad
            return not result.success

        except ContainerError as e:
            # Container error typically means the test failed
            logger.debug(f"Container error testing {commit_sha[:7]}: {e}")
            if log_callback:
                log_callback(f"   │ Container exited with error (test likely failed)")
            return True

        except Exception as e:
            # On unexpected errors, assume the commit is bad to be safe
            error_type = type(e).__name__
            logger.warning(f"Error testing commit {commit_sha[:7]} ({error_type}): {e}")
            
            # Log more details for debugging
            error_msg = str(e).lower()
            if "timeout" in error_msg:
                logger.warning(f"  → Test timed out for commit {commit_sha[:7]}")
                logger.warning("    • This is unexpected since timeouts are disabled")
                logger.warning("    • Check Docker daemon or network connectivity")
            elif "memory" in error_msg or "oom" in error_msg:
                logger.warning(f"  → Possible memory issue for commit {commit_sha[:7]}")
                logger.warning("    • Check Docker container memory limits")
            
            if log_callback:
                log_callback(f"   ⚠️ Error testing commit ({error_type}): {e}")
                log_callback("   │ Marking commit as BAD to continue bisect")
            return True

    def _get_commit_message(
        self,
        job: BisectJob,
        image_name: str,
        volume_name: str,
        commit_sha: str,
    ) -> Optional[str]:
        """Get the commit message for a given SHA (repo already cloned in volume)."""
        script = f'''#!/bin/bash
set -e

git config --global --add safe.directory /workspace/repo
cd /workspace/repo
git log -1 --pretty=%s {commit_sha}
'''
        try:
            result = self.docker_runner.run(
                image_name,
                ["bash", "-c", script],
                network_mode="bridge",
                working_dir="/workspace",
                volumes={volume_name: {"bind": "/workspace", "mode": "rw"}},
            )

            if result.success:
                return result.stdout_text.strip()

        except Exception as e:
            logger.warning(f"Failed to get commit message: {e}")

        return None


def build_runner_image(tag: Optional[str] = None) -> bool:
    """Build the bisect runner Docker image."""
    import docker
    from docker.errors import DockerException
    from pathlib import Path

    settings = get_settings()
    image_tag = tag or settings.docker_runner_image
    docker_dir = Path(__file__).parent.parent / "docker"
    if not docker_dir.exists():
        logger.error(f"Docker directory not found: {docker_dir}")
        return False

    logger.info(f"Building runner image: {image_tag}")

    try:
        client = docker.from_env()
        image, logs = client.images.build(
            path=str(docker_dir),
            dockerfile="Dockerfile.runner",
            tag=image_tag,
            rm=True,
        )

        for log in logs:
            if "stream" in log:
                logger.info(log["stream"].strip())

        logger.info(f"Successfully built image: {image_tag}")
        return True

    except DockerException as e:
        logger.error(f"Failed to build image: {e}")
        return False
