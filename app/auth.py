"""Authentication routes for GitHub OAuth."""

import secrets
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, HTTPException, Depends, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Simple in-memory session store (use Redis in production)
_sessions: dict[str, dict] = {}
_oauth_states: dict[str, float] = {}  # state -> timestamp


def generate_session_token() -> str:
    """Generate a secure session token."""
    return secrets.token_urlsafe(32)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """Get the current authenticated user from session."""
    session_token = request.cookies.get("session")
    if not session_token or session_token not in _sessions:
        return None
    
    session_data = _sessions[session_token]
    user_id = session_data.get("user_id")
    if not user_id:
        return None
    
    user = db.query(User).filter(User.id == user_id).first()
    return user


def require_auth(request: Request, db: Session = Depends(get_db)) -> User:
    """Require authentication, raise 401 if not logged in."""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@router.get("/login")
async def login():
    """Redirect to GitHub OAuth authorization page."""
    settings = get_settings()
    
    if not settings.github_client_id:
        raise HTTPException(
            status_code=500,
            detail="GitHub OAuth not configured. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET."
        )
    
    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = datetime.utcnow().timestamp()
    
    # Clean up old states (older than 10 minutes)
    now = datetime.utcnow().timestamp()
    expired = [s for s, ts in _oauth_states.items() if now - ts > 600]
    for s in expired:
        _oauth_states.pop(s, None)
    
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": f"{settings.base_url}/auth/callback",
        # Request repo scope to access repositories, branches, and commits
        "scope": "read:user user:email repo",
        "state": state,
    }
    
    query = urlencode(params)
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{query}")


@router.get("/callback")
async def callback(
    code: str = None,
    state: str = None,
    error: str = None,
    db: Session = Depends(get_db),
):
    """Handle GitHub OAuth callback."""
    settings = get_settings()
    
    if error:
        logger.warning(f"OAuth error: {error}")
        return RedirectResponse("/?error=oauth_denied")
    
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")
    
    # Verify state for CSRF protection
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid state parameter")
    _oauth_states.pop(state, None)
    
    # Exchange code for access token
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": f"{settings.base_url}/auth/callback",
            },
            headers={"Accept": "application/json"},
        )
        
        if token_response.status_code != 200:
            logger.error(f"Token exchange failed: {token_response.text}")
            return RedirectResponse("/?error=token_exchange_failed")
        
        token_data = token_response.json()
        access_token = token_data.get("access_token")
        
        if not access_token:
            logger.error(f"No access token in response: {token_data}")
            return RedirectResponse("/?error=no_access_token")
        
        # Get user info from GitHub
        user_response = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        
        if user_response.status_code != 200:
            logger.error(f"Failed to get user info: {user_response.text}")
            return RedirectResponse("/?error=user_info_failed")
        
        github_user = user_response.json()
        
        # Get user email (may need separate request if email is private)
        email = github_user.get("email")
        if not email:
            email_response = await client.get(
                "https://api.github.com/user/emails",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            if email_response.status_code == 200:
                emails = email_response.json()
                primary = next((e for e in emails if e.get("primary")), None)
                if primary:
                    email = primary.get("email")
    
    # Find or create user
    github_id = github_user["id"]
    user = db.query(User).filter(User.github_id == github_id).first()
    
    if user:
        # Update existing user
        user.github_login = github_user["login"]
        user.github_email = email
        user.github_avatar_url = github_user.get("avatar_url")
        user.access_token = access_token
        user.last_login_at = datetime.utcnow()
    else:
        # Create new user
        user = User(
            github_id=github_id,
            github_login=github_user["login"],
            github_email=email,
            github_avatar_url=github_user.get("avatar_url"),
            access_token=access_token,
            last_login_at=datetime.utcnow(),
        )
        db.add(user)
    
    db.commit()
    db.refresh(user)
    
    # Create session
    session_token = generate_session_token()
    _sessions[session_token] = {
        "user_id": user.id,
        "github_login": user.github_login,
        "created_at": datetime.utcnow().timestamp(),
    }
    
    logger.info(f"User {user.github_login} logged in successfully")
    
    response = RedirectResponse("/")
    response.set_cookie(
        key="session",
        value=session_token,
        httponly=True,
        secure=not settings.dev_mode,
        samesite="lax",
        max_age=86400 * 7,  # 7 days
    )
    return response


@router.get("/logout")
async def logout(request: Request):
    """Log out the current user."""
    session_token = request.cookies.get("session")
    if session_token:
        _sessions.pop(session_token, None)
    
    response = RedirectResponse("/")
    response.delete_cookie("session")
    return response


@router.get("/me")
async def me(request: Request, db: Session = Depends(get_db)):
    """Get the current authenticated user."""
    user = get_current_user(request, db)
    if not user:
        return {"authenticated": False}
    
    return {
        "authenticated": True,
        "user": {
            "id": user.id,
            "github_id": user.github_id,
            "github_login": user.github_login,
            "github_email": user.github_email,
            "github_avatar_url": user.github_avatar_url,
        },
    }

