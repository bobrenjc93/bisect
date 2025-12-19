"""GitHub App client for authentication and API interactions."""

import time

import jwt
import httpx

from app.config import get_settings


class GitHubAppClient:
    """Client for interacting with GitHub as a GitHub App."""

    BASE_URL = "https://api.github.com"

    def __init__(self):
        self.settings = get_settings()
        self._installation_tokens: dict[int, tuple[str, float]] = {}

    def _generate_jwt(self) -> str:
        """Generate a JWT for GitHub App authentication."""
        now = int(time.time())
        payload = {
            "iat": now - 60,  # Issued 60 seconds ago to account for clock drift
            "exp": now + 600,  # Expires in 10 minutes
            "iss": self.settings.github_app_id,
        }
        return jwt.encode(
            payload,
            self.settings.github_private_key,
            algorithm="RS256",
        )

    def _get_installation_token(self, installation_id: int) -> str:
        """Get an installation access token, using cache if valid."""
        if installation_id in self._installation_tokens:
            token, expires_at = self._installation_tokens[installation_id]
            # Use cached token if it has more than 5 minutes left
            if time.time() < expires_at - 300:
                return token

        jwt_token = self._generate_jwt()
        with httpx.Client() as client:
            response = client.post(
                f"{self.BASE_URL}/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            data = response.json()

        token = data["token"]
        expires_at = time.time() + 3000  # Cache for 50 min (tokens last 1 hour)

        self._installation_tokens[installation_id] = (token, expires_at)

        return token

    def _get_headers(self, installation_id: int) -> dict:
        """Get headers for API requests with installation token."""
        token = self._get_installation_token(installation_id)
        return {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get_repo_clone_url(
        self,
        owner: str,
        repo: str,
        installation_id: int,
    ) -> str:
        """Get the clone URL for a repository with authentication."""
        token = self._get_installation_token(installation_id)
        return f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"

    def get_commit_info(
        self,
        owner: str,
        repo: str,
        sha: str,
        installation_id: int,
    ) -> dict:
        """Get information about a specific commit."""
        with httpx.Client() as client:
            response = client.get(
                f"{self.BASE_URL}/repos/{owner}/{repo}/commits/{sha}",
                headers=self._get_headers(installation_id),
            )
            response.raise_for_status()
            return response.json()

