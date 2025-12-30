"""Docker-based bisect runner that spawns isolated containers for each commit test."""

import logging
import uuid
from typing import Optional, Callable

from dockerrun import (
    DockerRunner,
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


def _generate_build_and_test_script(test_command: str) -> str:
    """Generate the build_and_test.sh script for git bisect run.
    
    This script is executed by git bisect run for each commit.
    Exit codes:
    - 0 = good commit (test passes)
    - 1-124, 126-127 = bad commit (test fails)
    - 125 = skip this commit (untestable)
    """
    return f'''#!/bin/bash
# Auto-generated build and test script for git bisect
# Exit code 0 = good commit (test passes)
# Exit code 1-124, 126-127 = bad commit (test fails)
# Exit code 125 = skip this commit (untestable)
set -e
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
        Run a bisect job using git bisect run with build_and_test.sh.

        This approach:
        1. Clones the repository and writes build_and_test.sh with the user's test command
        2. Uses git bisect run ./build_and_test.sh to find the first bad commit
        3. Leverages git's built-in bisect logic for reliability and caching benefits

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

            # Clone the repository and write build_and_test.sh
            if log_callback:
                log_callback(f"📋 Cloning repository...")

            clone_success = self._clone_and_setup_bisect_script(job, image_name, volume_name, log_callback)
            if not clone_success:
                return BisectResult(
                    success=False,
                    error="Failed to clone repository or setup build_and_test.sh",
                )

            if log_callback:
                log_callback(f"📝 Created build_and_test.sh with test command")

            # Run git bisect run with build_and_test.sh
            return self._run_git_bisect(job, image_name, volume_name, log_callback)

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

    def _clone_and_setup_bisect_script(
        self,
        job: BisectJob,
        image_name: str,
        volume_name: str,
        log_callback: Optional[LogCallback] = None,
    ) -> bool:
        """Clone the repository and create build_and_test.sh script."""
        # Generate the build_and_test.sh content
        build_test_script = _generate_build_and_test_script(job.test_command)
        
        script = f'''#!/bin/bash
set -e

{GIT_INSTALL_SCRIPT}

git config --global user.email "bisect-bot@example.com"
git config --global user.name "Bisect Bot"
git config --global --add safe.directory /workspace/repo

echo "Cloning repository..."
git clone --progress "{job.repo_url}" /workspace/repo 2>&1
cd /workspace/repo

echo "Creating build_and_test.sh..."
cat > build_and_test.sh << 'BISECT_SCRIPT_EOF'
{build_test_script}BISECT_SCRIPT_EOF
chmod +x build_and_test.sh

echo "build_and_test.sh created successfully"
'''
        try:
            # Use streaming to show clone progress
            def on_output(chunk):
                text = chunk.decode().rstrip()
                if text and log_callback:
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

            if not result.success:
                stderr = result.stderr_text or "Unknown error"
                logger.error(f"Failed to clone repository: {stderr}")
                logger.error("-" * 50)
                logger.error("📋 POSSIBLE CAUSES:")
                if "authentication" in stderr.lower() or "permission" in stderr.lower():
                    logger.error("  → Repository access denied")
                    logger.error("    • Check GitHub App installation permissions")
                    logger.error("    • Ensure the App has access to the repository")
                else:
                    logger.error(f"  → Git error: {stderr}")
                logger.error("-" * 50)
                if log_callback:
                    log_callback(f"❌ Failed to clone repository: {stderr}")
                return False

            return True

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
            return False

    def _run_git_bisect(
        self,
        job: BisectJob,
        image_name: str,
        volume_name: str,
        log_callback: Optional[LogCallback] = None,
    ) -> BisectResult:
        """Run git bisect using git bisect run with build_and_test.sh.
        
        This leverages git's built-in bisect logic for reliability and gets
        caching benefits from the shell script approach.
        """
        output_lines = []

        def log(msg: str):
            output_lines.append(msg)
            if log_callback:
                log_callback(msg)

        log(f"")
        log(f"▶️ Starting git bisect run with build_and_test.sh...")

        # Script that runs git bisect start and git bisect run
        # Need to install git again since this is a fresh container (git was only installed
        # in the clone container, which is ephemeral - only the /workspace volume persists)
        script = f'''#!/bin/bash
set -e

{GIT_INSTALL_SCRIPT}

git config --global --add safe.directory /workspace/repo
cd /workspace/repo

echo "=== Starting git bisect ==="
git bisect start {job.bad_sha} {job.good_sha}

echo "=== Running git bisect with build_and_test.sh ==="
# Run bisect - git bisect run will execute build_and_test.sh for each commit
# and use exit codes to determine good/bad
git bisect run ./build_and_test.sh

echo "=== Bisect complete ==="
git bisect log
'''
        try:
            collected_output = []

            def on_output(chunk):
                text = chunk.decode().rstrip()
                if text:
                    collected_output.append(text)
                    if log_callback:
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

            full_output = result.stdout_text + (result.stderr_text or "")
            
            # Parse the output to find the culprit commit
            culprit_sha = None
            for line in full_output.split("\n"):
                if "is the first bad commit" in line:
                    parts = line.split()
                    if parts:
                        culprit_sha = parts[0]
                        break

            if culprit_sha:
                # Get the commit message
                culprit_message = self._get_commit_message(job, image_name, volume_name, culprit_sha)

                log(f"")
                log(f"🎯 Found first bad commit: {culprit_sha[:7]}")
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
            else:
                # Bisect didn't find a culprit
                error_msg = "Bisect did not find a culprit commit"
                if not result.success:
                    error_msg = f"Bisect failed: {result.stderr_text or 'Unknown error'}"
                
                log(f"")
                log(f"❌ {error_msg}")

                return BisectResult(
                    success=False,
                    error=error_msg,
                    output="\n".join(output_lines),
                )

        except ContainerError as e:
            logger.error(f"Container error during bisect: {e}")
            if log_callback:
                log_callback(f"❌ Container error during bisect: {e}")
            return BisectResult(
                success=False,
                error=f"Container error: {e}",
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

    def _get_commit_message(
        self,
        job: BisectJob,
        image_name: str,
        volume_name: str,
        commit_sha: str,
    ) -> Optional[str]:
        """Get the commit message for a given SHA (repo already cloned in volume)."""
        # Need to install git again since this is a fresh container
        script = f'''#!/bin/bash
set -e

{GIT_INSTALL_SCRIPT}

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
