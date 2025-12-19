"""Drop issue_number column

Revision ID: 002_drop_issue_number
Revises: 001_initial
Create Date: 2024-12-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002_drop_issue_number'
down_revision: Union[str, None] = '001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('bisect_jobs', 'issue_number')


def downgrade() -> None:
    op.add_column('bisect_jobs', sa.Column('issue_number', sa.Integer(), nullable=True, server_default='0'))

