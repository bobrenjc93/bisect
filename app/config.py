"""Configuration management for the GitHub Bisect Bot."""

from pathlib import Path
from functools import lru_cache
from typing import Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Development mode - relaxes security requirements for local testing
    dev_mode: bool = False

    github_app_id: str = "12345"  # Default for dev mode
    github_app_slug: str = "bisect-bot"  # App slug name for installation URL
    github_private_key_path: Path = Path("secrets/private-key.pem")
    github_webhook_secret: str = "dev-secret-at-least-16-chars"
    
    # GitHub OAuth settings (for user login)
    github_client_id: str = ""
    github_client_secret: str = ""
    
    # Session secret for signing cookies
    session_secret: str = "dev-session-secret-change-me-in-production"
    
    # Base URL for OAuth callbacks
    base_url: str = "http://localhost:8000"

    # PostgreSQL database URL
    database_url: str = "postgresql://bisect:changeme@postgres:5432/bisect"

    docker_runner_image: str = "bisect-runner:latest"
    bisect_timeout_seconds: Optional[int] = None  # No timeout - bisect can take as long as needed
    docker_client_timeout: Optional[int] = None  # No socket-level timeout for Docker operations
    docker_stream_timeout: Optional[int] = None  # No timeout for log stream reads
    docker_stream_retries: int = 5  # Number of retries for transient streaming errors
    max_concurrent_jobs: int = 4

    host: str = "0.0.0.0"
    port: int = 8000
    allowed_hosts: str = "*"
    encryption_key: Optional[str] = None

    @property
    def github_private_key(self) -> str:
        """Read the GitHub App private key from file."""
        if not self.github_private_key_path.exists():
            if self.dev_mode:
                # Return a dummy key for development
                return "-----BEGIN RSA PRIVATE KEY-----\nDUMMY\n-----END RSA PRIVATE KEY-----"
            raise FileNotFoundError(
                f"GitHub private key not found at {self.github_private_key_path}"
            )
        return self.github_private_key_path.read_text()
    
    @property
    def allowed_hosts_list(self) -> list[str]:
        """Get allowed hosts as a list."""
        if self.allowed_hosts == "*":
            return ["*"]
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]
    
    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        """Validate settings after all fields are set."""
        # In dev mode, relax validation requirements
        if not self.dev_mode:
            # Require strong webhook secret in production
            if len(self.github_webhook_secret) < 16:
                raise ValueError(
                    "GITHUB_WEBHOOK_SECRET should be at least 16 characters for security"
                )
        return self

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra fields from .env file


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
