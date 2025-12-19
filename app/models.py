"""SQLAlchemy models for the GitHub Bisect Bot."""

from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Text,
    Boolean,
    DateTime,
    ForeignKey,
    Enum,
    Date,
    UniqueConstraint,
    Index,
    TypeDecorator,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.encryption import encrypt_field, decrypt_field


class EncryptedText(TypeDecorator):
    """SQLAlchemy type that encrypts on write and decrypts on read."""
    
    impl = Text
    cache_ok = True
    
    def process_bind_param(self, value: Optional[str], dialect) -> Optional[str]:
        if value is None:
            return None
        return encrypt_field(value)
    
    def process_result_value(self, value: Optional[str], dialect) -> Optional[str]:
        if value is None:
            return None
        return decrypt_field(value)


class JobStatus(PyEnum):
    """Status of a bisect job."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class User(Base):
    """GitHub user who has authenticated via OAuth."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    github_id = Column(BigInteger, unique=True, nullable=False, index=True)
    github_login = Column(String(255), nullable=False, index=True)
    github_email = Column(String(255), nullable=True)
    github_avatar_url = Column(Text, nullable=True)
    # SECURITY: OAuth access token is encrypted at rest
    access_token = Column(EncryptedText, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    installations = relationship("Installation", back_populates="installed_by_user")


class Installation(Base):
    """GitHub App installation on a user or organization account."""

    __tablename__ = "installations"

    id = Column(Integer, primary_key=True)
    installation_id = Column(BigInteger, unique=True, nullable=False, index=True)
    account_type = Column(String(50), nullable=False)  # 'User' or 'Organization'
    account_login = Column(String(255), nullable=False, index=True)
    account_id = Column(BigInteger, nullable=False)
    installed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    suspended_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    installed_by_user = relationship("User", back_populates="installations")
    repositories = relationship("Repository", back_populates="installation", cascade="all, delete-orphan")


class Repository(Base):
    """Repository where the GitHub App is installed."""

    __tablename__ = "repositories"

    id = Column(Integer, primary_key=True)
    github_id = Column(BigInteger, unique=True, nullable=False, index=True)
    installation_id = Column(Integer, ForeignKey("installations.id", ondelete="CASCADE"), nullable=False, index=True)
    owner = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    full_name = Column(String(511), nullable=False, index=True)  # owner/name
    private = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)  # User can disable without uninstalling
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    installation = relationship("Installation", back_populates="repositories")
    bisect_jobs = relationship("BisectJob", back_populates="repository")
    usage_stats = relationship("UsageStat", back_populates="repository", cascade="all, delete-orphan")


class BisectJob(Base):
    """A bisect job requested by a user."""

    __tablename__ = "bisect_jobs"

    id = Column(Integer, primary_key=True)
    repository_id = Column(Integer, ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True, index=True)
    installation_id = Column(BigInteger, nullable=False)
    requested_by = Column(String(255), nullable=True)  # GitHub username who triggered

    repo_owner = Column(String(255), nullable=True)
    repo_name = Column(String(255), nullable=True)

    good_sha = Column(String(40), nullable=False)
    bad_sha = Column(String(40), nullable=False)
    test_command = Column(Text, nullable=False)
    docker_image = Column(String(255), nullable=True)  # Custom Docker image for bisect

    status = Column(Enum(JobStatus), default=JobStatus.PENDING, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    worker_id = Column(String(255), nullable=True, index=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    attempt_count = Column(Integer, default=0)

    culprit_sha = Column(String(40), nullable=True)
    culprit_message = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    output_log = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    repository = relationship("Repository", back_populates="bisect_jobs")


class UsageStat(Base):
    """Usage statistics for rate limiting and tracking."""

    __tablename__ = "usage_stats"

    id = Column(Integer, primary_key=True)
    repository_id = Column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    period_start = Column(Date, nullable=False, index=True)  # Start of the period (e.g., month)
    job_count = Column(Integer, default=0)
    total_duration_seconds = Column(Integer, default=0)

    repository = relationship("Repository", back_populates="usage_stats")

    __table_args__ = (
        UniqueConstraint("repository_id", "period_start", name="uq_usage_stats_repo_period"),
    )


class RateLimit(Base):
    """Rate limit configuration by tier."""

    __tablename__ = "rate_limits"

    id = Column(Integer, primary_key=True)
    tier = Column(String(50), unique=True, nullable=False)  # 'free', 'pro', 'enterprise'
    max_jobs_per_month = Column(Integer, nullable=False)  # -1 means unlimited
    max_job_duration_seconds = Column(Integer, nullable=False)
    max_concurrent_jobs = Column(Integer, nullable=False)

