"""Local bisect runner for testing and development without Docker."""

import logging
import tempfile
import shutil
from pathlib import Path
from typing import Optional, Union

from app.bisect_core import BisectJob, BisectResult, run_bisect, run_bisect_on_clone

logger = logging.getLogger(__name__)


class LocalBisectRunner:
    """Runs git bisect operations directly on the local filesystem."""

    def __init__(self, work_dir: Optional[Union[str, Path]] = None):
        self.work_dir = Path(work_dir) if work_dir else None

    def run_bisect(self, job: BisectJob) -> BisectResult:
        """Run a bisect job locally."""
        logger.info(f"Starting local bisect: good={job.good_sha}, bad={job.bad_sha}")

        if self.work_dir:
            work_dir = self.work_dir
            cleanup = False
        else:
            work_dir = Path(tempfile.mkdtemp(prefix="bisect_"))
            cleanup = True

        try:
            result = run_bisect_on_clone(
                repo_url=job.repo_url,
                work_dir=str(work_dir),
                good_sha=job.good_sha,
                bad_sha=job.bad_sha,
                test_command=job.test_command,
            )
            
            if result.success:
                logger.info(f"Bisect found culprit: {result.culprit_sha}")
            else:
                logger.warning(f"Bisect failed: {result.error}")
                
            return result
        finally:
            if cleanup and work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)

    def run_bisect_on_existing_repo(
        self,
        repo_path: Union[str, Path],
        good_sha: str,
        bad_sha: str,
        test_command: str,
    ) -> BisectResult:
        """Run bisect on an existing local repository."""
        logger.info(
            f"Running bisect on existing repo: {repo_path}, "
            f"good={good_sha}, bad={bad_sha}"
        )
        
        return run_bisect(
            repo_dir=str(repo_path),
            good_sha=good_sha,
            bad_sha=bad_sha,
            test_command=test_command,
        )

