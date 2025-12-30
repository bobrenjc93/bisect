"""FastAPI application for the GitHub Bisect Bot."""

import asyncio
import os
import logging
import socket
import time
from contextlib import asynccontextmanager
from collections import defaultdict
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.config import get_settings
from app.github_client import GitHubAppClient
from app.bisect_runner import BisectRunner
from app.bisect_core import BisectJob as BisectJobData
from app.database import get_db, SessionLocal
from app.models import BisectJob, JobStatus
from app.security import configure_secure_logging
from app.auth import router as auth_router
from app.api import router as api_router
from app.streaming import get_stream_manager, SyncStreamPublisher

configure_secure_logging(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}-{int(time.time())}"
STALE_JOB_THRESHOLD = timedelta(minutes=5)
HEARTBEAT_INTERVAL = 60
JOB_POLL_INTERVAL = 2  # Check for new pending jobs every 2 seconds
RECOVERY_SCAN_INTERVAL = 30  # Full recovery scan for stale jobs
MAX_JOB_ATTEMPTS = 3

settings = get_settings()
executor = ThreadPoolExecutor(max_workers=settings.max_concurrent_jobs)


def log_startup_diagnostics():
    """Log startup diagnostics to help identify configuration issues."""
    logger.info("=" * 60)
    logger.info("🔧 STARTUP DIAGNOSTICS")
    logger.info("=" * 60)
    
    # Configuration summary
    logger.info(f"  Max concurrent jobs: {settings.max_concurrent_jobs}")
    timeout_str = f"{settings.bisect_timeout_seconds}s" if settings.bisect_timeout_seconds else "disabled"
    logger.info(f"  Bisect timeout: {timeout_str}")
    logger.info(f"  Database URL: {settings.database_url.split('@')[0]}@***")  # Hide credentials
    
    # Check git availability
    try:
        from app.bisect_runner import BisectRunner
        runner = BisectRunner()
        if runner.check_docker_available():  # Note: this now checks git, not Docker
            logger.info("  ✅ Git: Available")
        else:
            logger.warning("  ⚠️  Git: NOT AVAILABLE - jobs will fail!")
    except Exception as e:
        logger.error(f"  ❌ Git check failed: {e}")
    
    logger.info("=" * 60)



class RateLimiter:
    """Simple rate limiter using sliding window."""
    
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)
    
    def is_allowed(self, key: str) -> bool:
        """Check if a request is allowed for the given key."""
        now = time.time()
        self._requests[key] = [ts for ts in self._requests[key] if ts > now - self.window_seconds]
        if len(self._requests[key]) >= self.max_requests:
            return False
        self._requests[key].append(now)
        return True
    
    def get_retry_after(self, key: str) -> int:
        """Get the number of seconds until the rate limit resets."""
        if not self._requests[key]:
            return 0
        oldest = min(self._requests[key])
        return max(0, int(self.window_seconds - (time.time() - oldest)))


job_query_limiter = RateLimiter(max_requests=300, window_seconds=60)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to all responses."""
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        
        # Allow UI assets for frontend pages, strict CSP for API
        if request.url.path.startswith("/api/"):
            response.headers["Content-Security-Policy"] = "default-src 'none'"
        else:
            # Relaxed CSP for UI pages (connect-src allows SSE streaming)
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' https: data:; "
                "connect-src 'self' https://api.github.com"
            )
        
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Don't cache API responses, but allow caching for static assets
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=3600"
        else:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        
        return response


running_jobs: dict[int, asyncio.Task] = {}
shutdown_event = asyncio.Event()
new_job_event = asyncio.Event()  # Triggered when a new job is created


def trigger_job_processing():
    """Signal that a new job is available for immediate processing."""
    new_job_event.set()


def update_heartbeat(db: Session, job_id: int) -> None:
    """Update the heartbeat timestamp for a running job."""
    job = db.query(BisectJob).filter(BisectJob.id == job_id).first()
    if job and job.status == JobStatus.RUNNING:
        job.heartbeat_at = datetime.utcnow()
        db.commit()


def run_bisect_job_sync(
    job_id: int,
    job_data: BisectJobData,
    owner: str,
    repo: str,
    event_loop: asyncio.AbstractEventLoop,
) -> None:
    """Run a bisect job synchronously (called from thread pool)."""
    logger.info(f"[{WORKER_ID}] Starting bisect job {job_id} for {owner}/{repo}")
    
    bisect_runner = BisectRunner()
    db = SessionLocal()
    
    # Create a streaming publisher for real-time output
    stream_publisher = SyncStreamPublisher(job_id, event_loop)
    
    try:
        job = db.query(BisectJob).filter(BisectJob.id == job_id).first()
        if job:
            job.status = JobStatus.RUNNING
            job.started_at = datetime.utcnow()
            job.heartbeat_at = datetime.utcnow()
            job.worker_id = WORKER_ID
            job.attempt_count = (job.attempt_count or 0) + 1
            db.commit()
        
        stream_publisher.publish_status("running")
        
        # Run bisect with streaming callback
        result = bisect_runner.run_bisect(
            job_data,
            log_callback=stream_publisher.publish_log,
        )
        
        job = db.query(BisectJob).filter(BisectJob.id == job_id).first()
        if job:
            job.status = JobStatus.SUCCESS if result.success else JobStatus.FAILED
            job.completed_at = datetime.utcnow()
            job.culprit_sha = result.culprit_sha
            job.culprit_message = result.culprit_message
            job.error_message = result.error
            job.output_log = result.output
            db.commit()
        
        # Publish final result
        if result.success:
            stream_publisher.publish_status("success")
            logger.info(f"✅ Job {job_id} completed successfully - culprit: {result.culprit_sha[:7] if result.culprit_sha else 'N/A'}")
            if result.culprit_sha:
                stream_publisher.publish_log(f"\n🎯 Found culprit: {result.culprit_sha[:7]}")
                if result.culprit_message:
                    stream_publisher.publish_log(f"📝 Message: {result.culprit_message}")
        else:
            stream_publisher.publish_status("failed")
            error_msg = result.error or "Unknown error"
            
            logger.error("=" * 60)
            logger.error(f"❌ JOB FAILED: Job {job_id} for {owner}/{repo}")
            logger.error("=" * 60)
            logger.error(f"  Error: {error_msg}")
            
            # Provide actionable diagnostics based on error
            error_lower = error_msg.lower()
            logger.error("-" * 60)
            logger.error("📋 TROUBLESHOOTING SUGGESTIONS:")
            
            if "commit list" in error_lower:
                logger.error("  → Failed to get commits between good and bad:")
                logger.error("    • Verify both SHA hashes exist in the repository")
                logger.error("    • Ensure good_sha is an ancestor of bad_sha")
                logger.error("    • Check git history: git log --oneline good_sha..bad_sha")
            elif "no commits" in error_lower:
                logger.error("  → No commits found to bisect:")
                logger.error("    • The good_sha and bad_sha might be the same")
                logger.error("    • Or good_sha is not an ancestor of bad_sha")
                logger.error("    • Verify commit order: good should come before bad")
            elif "clone" in error_lower or "git" in error_lower:
                logger.error("  → Git/clone error:")
                logger.error("    • Check if the repository URL is accessible")
                logger.error("    • Verify GitHub App permissions")
            else:
                logger.error("  → General failure:")
                logger.error("    • Check the output_log for detailed error messages")
                logger.error("    • Verify your test_command works locally")
            
            logger.error("-" * 60)
            logger.error("🔄 TO RETRY THIS JOB:")
            logger.error(f"    • Via API: POST /api/jobs/{job_id}/retry")
            logger.error("    • Via UI: Click 'Retry' on the job details page")
            logger.error("=" * 60)
            
            if result.error:
                stream_publisher.publish_log(f"\n❌ Error: {result.error}")
        
        logger.info(f"[{WORKER_ID}] Bisect job {job_id} completed: success={result.success}")
        
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        
        logger.error("=" * 60)
        logger.error(f"❌ JOB FAILED: Job {job_id} for {owner}/{repo}")
        logger.error("=" * 60)
        logger.error(f"  Error Type: {error_type}")
        logger.error(f"  Error Message: {error_msg}")
        logger.exception("  Full traceback:")
        
        # Provide actionable diagnostics based on error type
        logger.error("-" * 60)
        logger.error("📋 TROUBLESHOOTING SUGGESTIONS:")
        
        error_lower = error_msg.lower()
        if "clone" in error_lower or "git" in error_lower:
            logger.error("  → Git/Repository error detected:")
            logger.error("    • Verify the repository URL is correct and accessible")
            logger.error("    • Check GitHub App installation permissions")
            logger.error("    • Ensure good_sha and bad_sha are valid commit hashes")
        elif "timeout" in error_lower:
            logger.error("  → Timeout error detected:")
            logger.error("    • Check if the repository is too large")
            logger.error("    • Verify network is stable for large repository clones")
        elif "permission" in error_lower or "denied" in error_lower:
            logger.error("  → Permission error detected:")
            logger.error("    • Check file permissions")
            logger.error("    • Verify GitHub App has correct repository access")
        else:
            logger.error("  → General troubleshooting:")
            logger.error("    • Check the application logs for more details")
            logger.error("    • Verify your test_command runs correctly")
            logger.error("    • Try running the test locally first")
        
        logger.error("-" * 60)
        logger.error("🔄 TO RETRY THIS JOB:")
        logger.error(f"    • Via API: POST /api/jobs/{job_id}/retry")
        logger.error("    • Via UI: Click 'Retry' on the job details page")
        logger.error("=" * 60)
        
        stream_publisher.publish_log(f"\n💥 Exception ({error_type}): {error_msg}")
        stream_publisher.publish_status("failed")
        
        job = db.query(BisectJob).filter(BisectJob.id == job_id).first()
        if job:
            job.status = JobStatus.FAILED
            job.completed_at = datetime.utcnow()
            job.error_message = f"{error_type}: {error_msg}"
            db.commit()
    
    finally:
        stream_publisher.mark_complete()
        db.close()


async def run_bisect_job_async(
    job_id: int,
    job_data: BisectJobData,
    owner: str,
    repo: str,
) -> None:
    """Run bisect job in thread pool to avoid blocking the event loop."""
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            executor,
            run_bisect_job_sync,
            job_id,
            job_data,
            owner,
            repo,
            loop,
        )
    finally:
        running_jobs.pop(job_id, None)
        # Schedule cleanup of stream buffer after a delay
        asyncio.create_task(cleanup_stream_after_delay(job_id, delay=300))


async def cleanup_stream_after_delay(job_id: int, delay: int = 300) -> None:
    """Clean up stream buffer after a delay to allow late subscribers."""
    await asyncio.sleep(delay)
    await get_stream_manager().cleanup(job_id)


async def heartbeat_loop() -> None:
    """Periodically update heartbeats for all running jobs on this instance."""
    logger.info(f"[{WORKER_ID}] Starting heartbeat loop")
    while not shutdown_event.is_set():
        try:
            if running_jobs:
                db = SessionLocal()
                try:
                    for job_id in list(running_jobs.keys()):
                        update_heartbeat(db, job_id)
                finally:
                    db.close()
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
        
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=HEARTBEAT_INTERVAL)
            break
        except asyncio.TimeoutError:
            pass


async def job_poll_loop() -> None:
    """Fast loop to pick up new pending jobs immediately."""
    logger.info(f"[{WORKER_ID}] Starting job poll loop (interval={JOB_POLL_INTERVAL}s)")
    
    # Short initial delay to let startup complete
    await asyncio.sleep(1)
    
    while not shutdown_event.is_set():
        # Clear the new job event before processing
        new_job_event.clear()
        
        try:
            await process_pending_jobs()
        except Exception as e:
            logger.error(f"Job poll error: {e}")
        
        # Wait for either: shutdown, new job signal, or timeout
        try:
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(shutdown_event.wait()),
                    asyncio.create_task(new_job_event.wait()),
                ],
                timeout=JOB_POLL_INTERVAL,
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Cancel pending tasks
            for task in pending:
                task.cancel()
            # Check if shutdown was triggered
            if shutdown_event.is_set():
                break
        except Exception:
            await asyncio.sleep(JOB_POLL_INTERVAL)


async def job_recovery_loop() -> None:
    """Periodically scan for and recover stale/orphaned jobs."""
    logger.info(f"[{WORKER_ID}] Starting job recovery loop (interval={RECOVERY_SCAN_INTERVAL}s)")
    
    # Wait a bit before first scan to let other instances stabilize
    await asyncio.sleep(5)
    
    while not shutdown_event.is_set():
        try:
            await recover_stale_jobs()
        except Exception as e:
            logger.error(f"Job recovery error: {e}")
        
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=RECOVERY_SCAN_INTERVAL)
            break
        except asyncio.TimeoutError:
            pass


async def start_job(job: BisectJob, db: Session) -> bool:
    """Start a pending job. Returns True if started, False otherwise."""
    github_client = GitHubAppClient()
    
    if len(running_jobs) >= settings.max_concurrent_jobs:
        return False
    
    if job.id in running_jobs:
        return False
    
    if not job.repo_owner or not job.repo_name:
        logger.error("=" * 50)
        logger.error(f"❌ JOB {job.id} FAILED TO START: Missing repository info")
        logger.error("=" * 50)
        logger.error("  → repo_owner and repo_name are required")
        logger.error("  → Check how the job was created")
        logger.error("=" * 50)
        job.status = JobStatus.FAILED
        job.error_message = "Job missing repo info (repo_owner or repo_name is null)"
        db.commit()
        return False
    
    try:
        clone_url = github_client.get_repo_clone_url(
            job.repo_owner, job.repo_name, job.installation_id
        )
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        job.attempt_count = (job.attempt_count or 0) + 1
        
        logger.error("=" * 50)
        logger.error(f"❌ JOB {job.id} FAILED TO START: Cannot get clone URL")
        logger.error("=" * 50)
        logger.error(f"  Repository: {job.repo_owner}/{job.repo_name}")
        logger.error(f"  Installation ID: {job.installation_id}")
        logger.error(f"  Error ({error_type}): {error_msg}")
        logger.error(f"  Attempt: {job.attempt_count}/{MAX_JOB_ATTEMPTS}")
        logger.error("-" * 50)
        logger.error("📋 POSSIBLE CAUSES:")
        if "installation" in error_msg.lower():
            logger.error("  → GitHub App installation issue")
            logger.error("    • The installation may have been removed")
            logger.error("    • Re-install the GitHub App on the repository")
        elif "not found" in error_msg.lower() or "404" in error_msg:
            logger.error("  → Repository not found or no access")
            logger.error("    • Check if repository exists")
            logger.error("    • Verify GitHub App has access to this repository")
        elif "token" in error_msg.lower() or "authentication" in error_msg.lower():
            logger.error("  → Authentication/token error")
            logger.error("    • Check GitHub App private key")
            logger.error("    • Verify GITHUB_APP_ID is correct")
        else:
            logger.error("  → General GitHub API error")
            logger.error("    • Check application logs for details")
        logger.error("=" * 50)
        
        if job.attempt_count >= MAX_JOB_ATTEMPTS:
            job.status = JobStatus.FAILED
            job.error_message = f"Max attempts ({MAX_JOB_ATTEMPTS}) exceeded. Last error: {error_msg}"
            logger.error(f"  ⛔ Job marked as FAILED after {MAX_JOB_ATTEMPTS} attempts")
        else:
            logger.info(f"  🔄 Job will be retried (attempt {job.attempt_count}/{MAX_JOB_ATTEMPTS})")
        db.commit()
        return False
    
    logger.info(f"[{WORKER_ID}] Starting job {job.id} for {job.repo_owner}/{job.repo_name}")
    
    job_data = BisectJobData(
        repo_url=clone_url,
        good_sha=job.good_sha,
        bad_sha=job.bad_sha,
        test_command=job.test_command,
        docker_image=job.docker_image,
    )
    
    task = asyncio.create_task(
        run_bisect_job_async(
            job_id=job.id,
            job_data=job_data,
            owner=job.repo_owner,
            repo=job.repo_name,
        )
    )
    running_jobs[job.id] = task
    return True


async def process_pending_jobs() -> None:
    """Fast loop to pick up new pending jobs immediately (no age requirement)."""
    if len(running_jobs) >= settings.max_concurrent_jobs:
        return  # At capacity, skip
    
    db = SessionLocal()
    try:
        # Get pending jobs ordered by creation time (FIFO)
        pending_jobs = db.query(BisectJob).filter(
            BisectJob.status == JobStatus.PENDING
        ).order_by(BisectJob.created_at).with_for_update(skip_locked=True).limit(
            settings.max_concurrent_jobs - len(running_jobs)
        ).all()
        
        for job in pending_jobs:
            if len(running_jobs) >= settings.max_concurrent_jobs:
                break
            await start_job(job, db)
    finally:
        db.close()


async def recover_stale_jobs() -> None:
    """Recover stale jobs that appear to be stuck (running but no heartbeat)."""
    if len(running_jobs) >= settings.max_concurrent_jobs:
        return  # At capacity, skip
    
    db = SessionLocal()
    try:
        stale_threshold = datetime.utcnow() - STALE_JOB_THRESHOLD
        
        # Find jobs that are marked as running but haven't had a heartbeat
        stale_jobs = db.query(BisectJob).filter(
            and_(
                BisectJob.status == JobStatus.RUNNING,
                BisectJob.heartbeat_at < stale_threshold,
                BisectJob.attempt_count < MAX_JOB_ATTEMPTS,
            )
        ).with_for_update(skip_locked=True).limit(5).all()
        
        for job in stale_jobs:
            if len(running_jobs) >= settings.max_concurrent_jobs:
                break
            
            if job.id in running_jobs:
                continue
            
            logger.warning("=" * 50)
            logger.warning(f"⚠️  RECOVERING STALE JOB {job.id}")
            logger.warning("=" * 50)
            logger.warning(f"  Repository: {job.repo_owner}/{job.repo_name}")
            logger.warning(f"  Previous worker: {job.worker_id}")
            logger.warning(f"  Last heartbeat: {job.heartbeat_at}")
            logger.warning(f"  Attempt count: {job.attempt_count}")
            logger.warning("-" * 50)
            logger.warning("  → This job appeared stuck (no heartbeat)")
            logger.warning("  → Resetting to PENDING for retry")
            if job.attempt_count >= MAX_JOB_ATTEMPTS - 1:
                logger.warning(f"  ⚠️  This is the last retry attempt!")
            logger.warning("=" * 50)
            
            # Reset to pending so it can be picked up
            job.status = JobStatus.PENDING
            job.worker_id = None
            job.heartbeat_at = None
            db.commit()
            
            # Start it immediately
            await start_job(job, db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info(f"[{WORKER_ID}] GitHub Bisect Bot starting up...")
    
    # Log startup diagnostics to help identify issues early
    log_startup_diagnostics()
    
    heartbeat_task = asyncio.create_task(heartbeat_loop())
    job_poll_task = asyncio.create_task(job_poll_loop())
    recovery_task = asyncio.create_task(job_recovery_loop())
    
    yield
    
    logger.info("=" * 60)
    logger.info(f"[{WORKER_ID}] 🛑 SHUTTING DOWN...")
    logger.info("=" * 60)
    shutdown_event.set()
    
    heartbeat_task.cancel()
    job_poll_task.cancel()
    recovery_task.cancel()
    
    if running_jobs:
        logger.info(f"  ⚠️  {len(running_jobs)} job(s) were running during shutdown")
        db = SessionLocal()
        try:
            for job_id in list(running_jobs.keys()):
                job = db.query(BisectJob).filter(BisectJob.id == job_id).first()
                if job and job.status == JobStatus.RUNNING:
                    job.status = JobStatus.PENDING
                    job.worker_id = None
                    job.heartbeat_at = None
                    logger.info(f"  → Job {job_id} ({job.repo_owner}/{job.repo_name}): Reset to PENDING for recovery")
            db.commit()
            logger.info("  ℹ️  These jobs will be automatically picked up when the service restarts")
        finally:
            db.close()
    else:
        logger.info("  ✅ No jobs were running during shutdown")
    
    # Log summary of pending jobs in database
    db = SessionLocal()
    try:
        pending_count = db.query(BisectJob).filter(BisectJob.status == JobStatus.PENDING).count()
        failed_count = db.query(BisectJob).filter(BisectJob.status == JobStatus.FAILED).count()
        if pending_count > 0:
            logger.info(f"  📋 {pending_count} job(s) are pending in the queue")
        if failed_count > 0:
            logger.warning(f"  ❌ {failed_count} job(s) have failed - check logs for details")
            logger.warning("     → View failed jobs: GET /api/jobs?status=failed")
            logger.warning("     → Retry via: POST /api/jobs/<id>/retry")
    finally:
        db.close()
    
    for job_id, task in running_jobs.items():
        task.cancel()
    
    executor.shutdown(wait=False)
    logger.info("=" * 60)
    logger.info(f"[{WORKER_ID}] ✅ Shutdown complete")
    logger.info("=" * 60)


app = FastAPI(
    title="GitHub Bisect Bot",
    description="A GitHub App that performs git bisection to find the commit that introduced a bug",
    version="1.0.0",
    lifespan=lifespan,
    # Enable docs in dev mode or when not bound to all interfaces
    docs_url="/docs" if (settings.dev_mode or settings.host != "0.0.0.0") else None,
    redoc_url="/redoc" if settings.dev_mode else None,
)

app.add_middleware(SecurityHeadersMiddleware)

if settings.host == "0.0.0.0":
    allowed_hosts = ["*"]
else:
    allowed_hosts = ["localhost", "127.0.0.1", settings.host]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

# Include auth and API routers
app.include_router(auth_router)
app.include_router(api_router)

# Mount static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

bisect_runner = BisectRunner()


def get_client_ip(request: Request) -> str:
    """Get the client IP address, handling proxies."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


@app.get("/")
async def root(request: Request):
    """Serve the main UI."""
    # Check if the client accepts HTML
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        index_file = Path(__file__).parent / "static" / "index.html"
        if index_file.exists():
            return FileResponse(index_file, media_type="text/html")
    
    # Return JSON for API clients
    response = {"status": "ok", "service": "github-bisect-bot", "worker_id": WORKER_ID}
    if settings.dev_mode:
        response["dev_mode"] = True
    return response


@app.get("/health")
async def health(request: Request):
    """Detailed health check."""
    client_ip = get_client_ip(request)
    
    if not job_query_limiter.is_allowed(f"health:{client_ip}"):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(job_query_limiter.get_retry_after(f"health:{client_ip}"))}
        )
    
    git_available = bisect_runner.check_docker_available()  # checks git availability
    return {
        "status": "healthy" if git_available else "degraded",
        "git_available": git_available,
        "worker_id": WORKER_ID,
        "running_jobs": len(running_jobs),
        "max_concurrent_jobs": settings.max_concurrent_jobs,
    }


@app.get("/stats")
async def stats(request: Request, db: Session = Depends(get_db)):
    """Get job statistics from database."""
    client_ip = get_client_ip(request)
    
    if not job_query_limiter.is_allowed(f"stats:{client_ip}"):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(job_query_limiter.get_retry_after(f"stats:{client_ip}"))}
        )
    
    pending = db.query(BisectJob).filter(BisectJob.status == JobStatus.PENDING).count()
    running = db.query(BisectJob).filter(BisectJob.status == JobStatus.RUNNING).count()
    completed = db.query(BisectJob).filter(BisectJob.status == JobStatus.SUCCESS).count()
    failed = db.query(BisectJob).filter(BisectJob.status == JobStatus.FAILED).count()
    
    return {
        "pending": pending,
        "running": running,
        "completed": completed,
        "failed": failed,
        "running_on_this_instance": len(running_jobs),
    }


@app.get("/job/{job_id}")
async def get_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    """Get the status of a specific job."""
    client_ip = get_client_ip(request)
    
    if not job_query_limiter.is_allowed(f"job:{client_ip}"):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(job_query_limiter.get_retry_after(f"job:{client_ip}"))}
        )
    
    job = db.query(BisectJob).filter(BisectJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return {
        "id": job.id,
        "status": job.status.value,
        "good_sha": job.good_sha,
        "bad_sha": job.bad_sha,
        "culprit_sha": job.culprit_sha,
        "error": job.error_message,
        "worker_id": job.worker_id,
        "attempt_count": job.attempt_count,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "heartbeat_at": job.heartbeat_at.isoformat() if job.heartbeat_at else None,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
