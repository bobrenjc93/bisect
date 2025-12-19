"""Initial schema

Revision ID: 001_initial
Revises: 
Create Date: 2024-12-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create job_status enum type explicitly first with IF NOT EXISTS equivalent
    op.execute("DO $$ BEGIN CREATE TYPE jobstatus AS ENUM ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'TIMEOUT', 'CANCELLED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;")
    
    # Use postgresql.ENUM with create_type=False since we created it manually above
    job_status_enum = postgresql.ENUM(
        'PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'TIMEOUT', 'CANCELLED',
        name='jobstatus',
        create_type=False
    )

    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('github_id', sa.BigInteger(), nullable=False),
        sa.Column('github_login', sa.String(length=255), nullable=False),
        sa.Column('github_email', sa.String(length=255), nullable=True),
        sa.Column('github_avatar_url', sa.Text(), nullable=True),
        sa.Column('access_token', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_users_github_id', 'users', ['github_id'], unique=True)
    op.create_index('idx_users_github_login', 'users', ['github_login'], unique=False)

    # Create installations table
    op.create_table(
        'installations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('installation_id', sa.BigInteger(), nullable=False),
        sa.Column('account_type', sa.String(length=50), nullable=False),
        sa.Column('account_login', sa.String(length=255), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('installed_by_user_id', sa.Integer(), nullable=True),
        sa.Column('suspended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['installed_by_user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_installations_installation_id', 'installations', ['installation_id'], unique=True)
    op.create_index('idx_installations_account_login', 'installations', ['account_login'], unique=False)

    # Create repositories table
    op.create_table(
        'repositories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('github_id', sa.BigInteger(), nullable=False),
        sa.Column('installation_id', sa.Integer(), nullable=False),
        sa.Column('owner', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=511), nullable=False),
        sa.Column('private', sa.Boolean(), nullable=True, default=False),
        sa.Column('enabled', sa.Boolean(), nullable=True, default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['installation_id'], ['installations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_repositories_github_id', 'repositories', ['github_id'], unique=True)
    op.create_index('idx_repositories_full_name', 'repositories', ['full_name'], unique=False)
    op.create_index('idx_repositories_installation_id', 'repositories', ['installation_id'], unique=False)

    # Create bisect_jobs table
    op.create_table(
        'bisect_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('repository_id', sa.Integer(), nullable=True),
        sa.Column('installation_id', sa.BigInteger(), nullable=False),
        sa.Column('issue_number', sa.Integer(), nullable=False),
        sa.Column('requested_by', sa.String(length=255), nullable=True),
        sa.Column('repo_owner', sa.String(length=255), nullable=True),
        sa.Column('repo_name', sa.String(length=255), nullable=True),
        sa.Column('good_sha', sa.String(length=40), nullable=False),
        sa.Column('bad_sha', sa.String(length=40), nullable=False),
        sa.Column('test_command', sa.Text(), nullable=False),
        sa.Column('status', job_status_enum, nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('worker_id', sa.String(length=255), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempt_count', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('culprit_sha', sa.String(length=40), nullable=True),
        sa.Column('culprit_message', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('output_log', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_bisect_jobs_repository_id', 'bisect_jobs', ['repository_id'], unique=False)
    op.create_index('idx_bisect_jobs_status', 'bisect_jobs', ['status'], unique=False)
    op.create_index('idx_bisect_jobs_created_at', 'bisect_jobs', ['created_at'], unique=False)
    op.create_index('idx_bisect_jobs_worker_id', 'bisect_jobs', ['worker_id'], unique=False)
    op.create_index('idx_bisect_jobs_heartbeat', 'bisect_jobs', ['heartbeat_at'], unique=False)

    # Create usage_stats table
    op.create_table(
        'usage_stats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('repository_id', sa.Integer(), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('job_count', sa.Integer(), nullable=True, default=0),
        sa.Column('total_duration_seconds', sa.Integer(), nullable=True, default=0),
        sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('repository_id', 'period_start', name='uq_usage_stats_repo_period')
    )
    op.create_index('idx_usage_stats_period', 'usage_stats', ['period_start'], unique=False)

    # Create rate_limits table
    op.create_table(
        'rate_limits',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tier', sa.String(length=50), nullable=False),
        sa.Column('max_jobs_per_month', sa.Integer(), nullable=False),
        sa.Column('max_job_duration_seconds', sa.Integer(), nullable=False),
        sa.Column('max_concurrent_jobs', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tier')
    )

    # Insert default rate limits
    op.execute("""
        INSERT INTO rate_limits (tier, max_jobs_per_month, max_job_duration_seconds, max_concurrent_jobs) VALUES
        ('free', 50, 1800, 1),
        ('pro', 500, 3600, 3),
        ('enterprise', -1, 7200, 10)
    """)


def downgrade() -> None:
    op.drop_table('rate_limits')
    op.drop_index('idx_usage_stats_period', table_name='usage_stats')
    op.drop_table('usage_stats')
    op.drop_index('idx_bisect_jobs_heartbeat', table_name='bisect_jobs')
    op.drop_index('idx_bisect_jobs_worker_id', table_name='bisect_jobs')
    op.drop_index('idx_bisect_jobs_created_at', table_name='bisect_jobs')
    op.drop_index('idx_bisect_jobs_status', table_name='bisect_jobs')
    op.drop_index('idx_bisect_jobs_repository_id', table_name='bisect_jobs')
    op.drop_table('bisect_jobs')
    op.drop_index('idx_repositories_installation_id', table_name='repositories')
    op.drop_index('idx_repositories_full_name', table_name='repositories')
    op.drop_index('idx_repositories_github_id', table_name='repositories')
    op.drop_table('repositories')
    op.drop_index('idx_installations_account_login', table_name='installations')
    op.drop_index('idx_installations_installation_id', table_name='installations')
    op.drop_table('installations')
    op.drop_index('idx_users_github_login', table_name='users')
    op.drop_index('idx_users_github_id', table_name='users')
    op.drop_table('users')
    
    # Drop the enum type
    sa.Enum(name='jobstatus').drop(op.get_bind(), checkfirst=True)

