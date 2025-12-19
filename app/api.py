"""API routes for the dashboard."""

import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.config import get_settings
from app.database import get_db
from app.models import User, Installation, Repository, BisectJob, JobStatus
from app.auth import get_current_user, require_auth
from app.streaming import get_stream_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/installations")
async def list_installations(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
):
    """List installations accessible to the current user."""
    # Get installations from GitHub using user's access token
    installations = []
    
    if user.access_token:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/user/installations",
                headers={
                    "Authorization": f"Bearer {user.access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            
            if response.status_code == 200:
                data = response.json()
                installations = data.get("installations", [])
    
    # Sync installations to database
    for inst_data in installations:
        inst = db.query(Installation).filter(
            Installation.installation_id == inst_data["id"]
        ).first()
        
        if not inst:
            inst = Installation(
                installation_id=inst_data["id"],
                account_type=inst_data["account"]["type"],
                account_login=inst_data["account"]["login"],
                account_id=inst_data["account"]["id"],
                installed_by_user_id=user.id,
            )
            db.add(inst)
        else:
            inst.account_login = inst_data["account"]["login"]
            inst.suspended_at = (
                datetime.fromisoformat(inst_data["suspended_at"].replace("Z", "+00:00"))
                if inst_data.get("suspended_at")
                else None
            )
    
    db.commit()
    
    return {
        "installations": [
            {
                "id": inst["id"],
                "account": {
                    "login": inst["account"]["login"],
                    "type": inst["account"]["type"],
                    "avatar_url": inst["account"]["avatar_url"],
                },
                "suspended": inst.get("suspended_at") is not None,
            }
            for inst in installations
        ]
    }


@router.get("/installations/{installation_id}/repositories")
async def list_repositories(
    installation_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
):
    """List repositories for an installation."""
    if not user.access_token:
        raise HTTPException(status_code=401, detail="No access token")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/user/installations/{installation_id}/repositories",
            headers={
                "Authorization": f"Bearer {user.access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail="Failed to fetch repositories"
            )
        
        data = response.json()
        repos = data.get("repositories", [])
    
    # Sync repositories to database
    inst = db.query(Installation).filter(
        Installation.installation_id == installation_id
    ).first()
    
    if inst:
        for repo_data in repos:
            repo = db.query(Repository).filter(
                Repository.github_id == repo_data["id"]
            ).first()
            
            if not repo:
                repo = Repository(
                    github_id=repo_data["id"],
                    installation_id=inst.id,
                    owner=repo_data["owner"]["login"],
                    name=repo_data["name"],
                    full_name=repo_data["full_name"],
                    private=repo_data["private"],
                )
                db.add(repo)
            else:
                repo.full_name = repo_data["full_name"]
                repo.private = repo_data["private"]
        
        db.commit()
    
    return {
        "repositories": [
            {
                "id": repo["id"],
                "name": repo["name"],
                "full_name": repo["full_name"],
                "private": repo["private"],
                "html_url": repo["html_url"],
                "description": repo.get("description"),
            }
            for repo in repos
        ]
    }


@router.get("/repositories")
async def list_all_repositories(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
):
    """List all repositories the user has access to via installations."""
    repos = (
        db.query(Repository)
        .join(Installation)
        .filter(Installation.installed_by_user_id == user.id)
        .all()
    )
    
    return {
        "repositories": [
            {
                "id": repo.id,
                "github_id": repo.github_id,
                "owner": repo.owner,
                "name": repo.name,
                "full_name": repo.full_name,
                "private": repo.private,
                "enabled": repo.enabled,
            }
            for repo in repos
        ]
    }


@router.patch("/repositories/{repo_id}")
async def update_repository(
    repo_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
):
    """Update repository settings (e.g., enable/disable)."""
    body = await request.json()
    
    repo = (
        db.query(Repository)
        .join(Installation)
        .filter(
            Repository.id == repo_id,
            Installation.installed_by_user_id == user.id,
        )
        .first()
    )
    
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    
    if "enabled" in body:
        repo.enabled = bool(body["enabled"])
    
    db.commit()
    
    return {
        "id": repo.id,
        "enabled": repo.enabled,
    }


@router.get("/jobs")
async def list_jobs(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
    limit: int = 50,
    offset: int = 0,
):
    """List bisect jobs for the current user's repositories."""
    # Get all repo names the user has access to
    user_repos = (
        db.query(Repository)
        .join(Installation)
        .filter(Installation.installed_by_user_id == user.id)
        .all()
    )
    repo_full_names = {f"{r.owner}/{r.name}" for r in user_repos}
    
    # Query jobs for those repos
    query = db.query(BisectJob).filter(
        (BisectJob.repo_owner + "/" + BisectJob.repo_name).in_(repo_full_names)
        if repo_full_names
        else False
    )
    
    # Also include jobs requested by the user
    query = db.query(BisectJob).filter(
        BisectJob.requested_by == user.github_login
    ).order_by(desc(BisectJob.created_at)).limit(limit).offset(offset)
    
    jobs = query.all()
    
    return {
        "jobs": [
            {
                "id": job.id,
                "repo": f"{job.repo_owner}/{job.repo_name}" if job.repo_owner else None,
                "good_sha": job.good_sha[:7] if job.good_sha else None,
                "bad_sha": job.bad_sha[:7] if job.bad_sha else None,
                "status": job.status.value if job.status else None,
                "culprit_sha": job.culprit_sha[:7] if job.culprit_sha else None,
                "error_message": job.error_message[:100] if job.error_message else None,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            }
            for job in jobs
        ],
        "total": len(jobs),
    }


@router.get("/jobs/{job_id}")
async def get_job_detail(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
):
    """Get detailed information about a specific job."""
    job = db.query(BisectJob).filter(BisectJob.id == job_id).first()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return {
        "id": job.id,
        "repo": f"{job.repo_owner}/{job.repo_name}" if job.repo_owner else None,
        "requested_by": job.requested_by,
        "good_sha": job.good_sha,
        "bad_sha": job.bad_sha,
        "test_command": job.test_command,
        "docker_image": job.docker_image,
        "status": job.status.value if job.status else None,
        "culprit_sha": job.culprit_sha,
        "culprit_message": job.culprit_message,
        "error_message": job.error_message,
        "output_log": job.output_log,
        "worker_id": job.worker_id,
        "attempt_count": job.attempt_count,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@router.get("/dashboard/stats")
async def dashboard_stats(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
):
    """Get dashboard statistics for the current user."""
    # Count installations
    installations_count = (
        db.query(Installation)
        .filter(Installation.installed_by_user_id == user.id)
        .count()
    )
    
    # Count repositories
    repos_count = (
        db.query(Repository)
        .join(Installation)
        .filter(Installation.installed_by_user_id == user.id)
        .count()
    )
    
    # Count jobs by status
    jobs_by_user = db.query(BisectJob).filter(
        BisectJob.requested_by == user.github_login
    )
    
    total_jobs = jobs_by_user.count()
    successful_jobs = jobs_by_user.filter(BisectJob.status == JobStatus.SUCCESS).count()
    failed_jobs = jobs_by_user.filter(BisectJob.status == JobStatus.FAILED).count()
    pending_jobs = jobs_by_user.filter(BisectJob.status == JobStatus.PENDING).count()
    running_jobs = jobs_by_user.filter(BisectJob.status == JobStatus.RUNNING).count()
    
    return {
        "installations": installations_count,
        "repositories": repos_count,
        "jobs": {
            "total": total_jobs,
            "successful": successful_jobs,
            "failed": failed_jobs,
            "pending": pending_jobs,
            "running": running_jobs,
        },
    }


@router.get("/github-app-url")
async def github_app_url():
    """Get the GitHub App installation URL."""
    settings = get_settings()
    return {
        "url": f"https://github.com/apps/{settings.github_app_slug}/installations/new",
        "app_slug": settings.github_app_slug,
    }


@router.get("/user/repos")
async def list_user_repos(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
    per_page: int = 100,
    page: int = 1,
):
    """List repositories the user has access to via their OAuth token."""
    if not user.access_token:
        raise HTTPException(status_code=401, detail="No access token")
    
    async with httpx.AsyncClient() as client:
        # First, get repositories from installations (GitHub App)
        installations_response = await client.get(
            "https://api.github.com/user/installations",
            headers={
                "Authorization": f"Bearer {user.access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        
        all_repos = []
        
        if installations_response.status_code == 200:
            installations = installations_response.json().get("installations", [])
            logger.info(f"Found {len(installations)} installations for user {user.github_login}")
            
            # Sync installations to database
            for installation in installations:
                inst_id = installation["id"]
                
                # Create or update installation in database
                inst = db.query(Installation).filter(
                    Installation.installation_id == inst_id
                ).first()
                
                if not inst:
                    inst = Installation(
                        installation_id=inst_id,
                        account_type=installation["account"]["type"],
                        account_login=installation["account"]["login"],
                        account_id=installation["account"]["id"],
                        installed_by_user_id=user.id,
                    )
                    db.add(inst)
                    db.commit()
                    db.refresh(inst)
                    logger.info(f"Created installation {inst_id} for account {installation['account']['login']}")
                
                repos_response = await client.get(
                    f"https://api.github.com/user/installations/{inst_id}/repositories",
                    headers={
                        "Authorization": f"Bearer {user.access_token}",
                        "Accept": "application/vnd.github+json",
                    },
                    params={"per_page": per_page},
                )
                if repos_response.status_code == 200:
                    repos = repos_response.json().get("repositories", [])
                    logger.info(f"Found {len(repos)} repositories for installation {inst_id}")
                    
                    for repo in repos:
                        # Sync repository to database
                        db_repo = db.query(Repository).filter(
                            Repository.github_id == repo["id"]
                        ).first()
                        
                        if not db_repo:
                            db_repo = Repository(
                                github_id=repo["id"],
                                installation_id=inst.id,
                                owner=repo["owner"]["login"],
                                name=repo["name"],
                                full_name=repo["full_name"],
                                private=repo["private"],
                            )
                            db.add(db_repo)
                        else:
                            db_repo.full_name = repo["full_name"]
                            db_repo.private = repo["private"]
                        
                        all_repos.append({
                            "id": repo["id"],
                            "name": repo["name"],
                            "full_name": repo["full_name"],
                            "private": repo["private"],
                            "html_url": repo["html_url"],
                            "description": repo.get("description"),
                            "default_branch": repo.get("default_branch", "main"),
                            "installation_id": inst_id,
                            "owner": repo["owner"]["login"],
                        })
                    
                    db.commit()
                else:
                    logger.warning(
                        f"Failed to fetch repos for installation {inst_id}: "
                        f"{repos_response.status_code} - {repos_response.text}"
                    )
        else:
            logger.warning(
                f"Failed to fetch installations for user {user.github_login}: "
                f"{installations_response.status_code} - {installations_response.text}"
            )
    
    return {"repositories": all_repos}


@router.get("/repos/{owner}/{repo}/branches")
async def list_branches(
    owner: str,
    repo: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
    per_page: int = 100,
):
    """List branches for a repository."""
    if not user.access_token:
        raise HTTPException(status_code=401, detail="No access token")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/branches",
            headers={
                "Authorization": f"Bearer {user.access_token}",
                "Accept": "application/vnd.github+json",
            },
            params={"per_page": per_page},
        )
        
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Repository not found")
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail="Failed to fetch branches"
            )
        
        branches = response.json()
    
    return {
        "branches": [
            {
                "name": branch["name"],
                "sha": branch["commit"]["sha"],
            }
            for branch in branches
        ]
    }


@router.get("/repos/{owner}/{repo}/commits")
async def list_commits(
    owner: str,
    repo: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
    sha: Optional[str] = None,
    per_page: int = 50,
):
    """List commits for a repository, optionally filtered by branch/sha."""
    if not user.access_token:
        raise HTTPException(status_code=401, detail="No access token")
    
    params = {"per_page": per_page}
    if sha:
        params["sha"] = sha
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/commits",
            headers={
                "Authorization": f"Bearer {user.access_token}",
                "Accept": "application/vnd.github+json",
            },
            params=params,
        )
        
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Repository not found")
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail="Failed to fetch commits"
            )
        
        commits = response.json()
    
    return {
        "commits": [
            {
                "sha": commit["sha"],
                "short_sha": commit["sha"][:7],
                "message": commit["commit"]["message"].split("\n")[0][:100],
                "author": commit["commit"]["author"]["name"],
                "date": commit["commit"]["author"]["date"],
                "html_url": commit["html_url"],
            }
            for commit in commits
        ]
    }


@router.post("/bisect")
async def create_bisect_job(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
):
    """Create a new bisect job from the UI."""
    body = await request.json()
    
    # Validate required fields
    required = ["owner", "repo", "good_sha", "bad_sha", "test_command", "installation_id"]
    for field in required:
        if not body.get(field):
            raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
    
    owner = body["owner"]
    repo = body["repo"]
    good_sha = body["good_sha"]
    bad_sha = body["bad_sha"]
    test_command = body["test_command"]
    installation_id = body["installation_id"]
    docker_image = body.get("docker_image")  # Optional custom Docker image
    
    # Create the job
    from app.models import BisectJob, JobStatus
    
    db_job = BisectJob(
        installation_id=installation_id,
        requested_by=user.github_login,
        repo_owner=owner,
        repo_name=repo,
        good_sha=good_sha,
        bad_sha=bad_sha,
        test_command=test_command,
        docker_image=docker_image,
        status=JobStatus.PENDING,
        attempt_count=0,
    )
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    
    logger.info(f"Created bisect job {db_job.id} for {owner}/{repo} by {user.github_login}")
    
    # Trigger immediate job processing (don't wait for poll interval)
    try:
        from app.main import trigger_job_processing
        trigger_job_processing()
    except ImportError:
        pass  # Graceful fallback if not available
    
    return {
        "id": db_job.id,
        "status": db_job.status.value,
        "message": "Bisect job created and queued for processing",
    }


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
):
    """Cancel a pending or running bisect job."""
    job = db.query(BisectJob).filter(BisectJob.id == job_id).first()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Verify user has access to this job
    if job.requested_by != user.github_login:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Only allow cancellation of pending or running jobs
    if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot cancel job with status '{job.status.value}'. Only pending or running jobs can be cancelled."
        )
    
    previous_status = job.status.value
    
    # Mark the job as cancelled
    job.status = JobStatus.CANCELLED
    job.completed_at = datetime.now()
    job.error_message = f"Job cancelled by user {user.github_login}"
    db.commit()
    
    logger.info(
        f"Job {job_id} cancelled by {user.github_login} "
        f"(previous status: {previous_status})"
    )
    
    # If the job was running, try to cancel the async task
    if previous_status == "running":
        try:
            from app.main import running_jobs
            if job_id in running_jobs:
                task = running_jobs[job_id]
                task.cancel()
                logger.info(f"Cancelled running task for job {job_id}")
        except ImportError:
            pass  # Graceful fallback if not available
        except Exception as e:
            logger.warning(f"Could not cancel running task for job {job_id}: {e}")
    
    # Notify any streaming clients
    try:
        stream_manager = get_stream_manager()
        # Use the async publish method from a sync context
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're in an async context, use create_task
                asyncio.create_task(
                    stream_manager.publish(job_id, "log", f"🛑 Job cancelled by {user.github_login}")
                )
                asyncio.create_task(
                    stream_manager.publish(job_id, "status", "cancelled")
                )
            else:
                loop.run_until_complete(
                    stream_manager.publish(job_id, "log", f"🛑 Job cancelled by {user.github_login}")
                )
                loop.run_until_complete(
                    stream_manager.publish(job_id, "status", "cancelled")
                )
        except RuntimeError:
            # No event loop, skip streaming notification
            pass
    except Exception as e:
        logger.warning(f"Could not notify streaming clients of cancellation: {e}")
    
    return {
        "id": job.id,
        "status": job.status.value,
        "previous_status": previous_status,
        "message": "Job cancelled successfully",
    }


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
):
    """Retry a failed bisect job with the same settings."""
    # Find the original job
    original_job = db.query(BisectJob).filter(BisectJob.id == job_id).first()
    
    if not original_job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Only allow retry if the job failed or was cancelled
    if original_job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
        raise HTTPException(status_code=400, detail="Only failed or cancelled jobs can be retried")
    
    # Create a new job with the same settings
    new_job = BisectJob(
        installation_id=original_job.installation_id,
        requested_by=user.github_login,
        repo_owner=original_job.repo_owner,
        repo_name=original_job.repo_name,
        good_sha=original_job.good_sha,
        bad_sha=original_job.bad_sha,
        test_command=original_job.test_command,
        docker_image=original_job.docker_image,
        status=JobStatus.PENDING,
        attempt_count=0,
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)
    
    logger.info(
        f"Created retry job {new_job.id} from original job {job_id} "
        f"for {new_job.repo_owner}/{new_job.repo_name} by {user.github_login}"
    )
    
    # Trigger immediate job processing
    try:
        from app.main import trigger_job_processing
        trigger_job_processing()
    except ImportError:
        pass  # Graceful fallback if not available
    
    return {
        "id": new_job.id,
        "original_job_id": job_id,
        "status": new_job.status.value,
        "message": "Retry job created and queued for processing",
    }


@router.get("/jobs/{job_id}/stream")
async def stream_job_output(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_auth),
):
    """Stream job output in real-time using Server-Sent Events.
    
    This endpoint provides real-time streaming of bisect job output.
    Connect using EventSource in the browser or any SSE client.
    
    Event types:
    - log: Log line from the bisect process
    - status: Job status update (running, success, failed)
    - progress: Progress update (step/total|message)
    - keepalive: Keepalive ping (sent every 30s)
    """
    # Verify job exists and user has access
    job = db.query(BisectJob).filter(BisectJob.id == job_id).first()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Verify user has access to this job (requested by them)
    if job.requested_by != user.github_login:
        raise HTTPException(status_code=403, detail="Access denied")
    
    stream_manager = get_stream_manager()
    
    async def event_generator():
        """Generate SSE events from the stream."""
        # First, send current job status
        yield f"event: status\ndata: {job.status.value}\n\n"
        
        # If job is already complete, send the stored output and close
        if job.status in (JobStatus.SUCCESS, JobStatus.FAILED, JobStatus.TIMEOUT, JobStatus.CANCELLED):
            if job.output_log:
                # Send stored output line by line
                for line in job.output_log.split('\n'):
                    yield f"event: log\ndata: {line}\n\n"
            yield "event: complete\ndata: Job already finished\n\n"
            return
        
        # For pending jobs, wait for them to start
        if job.status == JobStatus.PENDING:
            yield "event: log\ndata: ⏳ Waiting for job to start...\n\n"
        
        # Subscribe to the stream
        try:
            async for message in stream_manager.subscribe(job_id):
                if await request.is_disconnected():
                    break
                
                if message.type == "keepalive":
                    yield ": keepalive\n\n"
                else:
                    # Escape newlines in content for SSE
                    content = message.content.replace('\n', '\ndata: ')
                    yield f"event: {message.type}\ndata: {content}\n\n"
        except asyncio.CancelledError:
            pass
        
        # Send completion event
        yield "event: complete\ndata: Stream ended\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )

