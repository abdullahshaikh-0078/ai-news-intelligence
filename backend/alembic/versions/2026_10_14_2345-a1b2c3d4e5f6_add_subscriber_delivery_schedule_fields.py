"""add_subscriber_delivery_schedule_fields

Revision ID: a1b2c3d4e5f6
Revises: f8a9b0c1d2e3
Create Date: 2026-10-14 23:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'f8a9b0c1d2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'subscribers',
        sa.Column('preferred_time', sa.String(length=10), nullable=False, server_default='08:00'),
    )
    op.add_column(
        'subscribers',
        sa.Column('timezone', sa.String(length=100), nullable=False, server_default='Asia/Kolkata'),
    )
    op.add_column(
        'subscribers',
        sa.Column('preferred_weekday', sa.String(length=20), nullable=True, server_default='MONDAY'),
    )
    op.add_column(
        'subscribers',
        sa.Column('content_preferences', sa.JSON(), nullable=False, server_default='{}'),
    )
    op.create_index(
        op.f('ix_subscribers_active_frequency'),
        'subscribers',
        ['is_active', 'frequency'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_subscribers_active_frequency'),
        table_name='subscribers',
    )
    op.drop_column('subscribers', 'content_preferences')
    op.drop_column('subscribers', 'preferred_weekday')
    op.drop_column('subscribers', 'timezone')
    op.drop_column('subscribers', 'preferred_time')
