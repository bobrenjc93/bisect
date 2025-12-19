"""Add docker_image column to bisect_jobs

Revision ID: 20241223_000001
Revises: 002_drop_issue_number
Create Date: 2024-12-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20241223_000001'
down_revision: Union[str, None] = '002_drop_issue_number'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'bisect_jobs',
        sa.Column('docker_image', sa.String(255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('bisect_jobs', 'docker_image')

