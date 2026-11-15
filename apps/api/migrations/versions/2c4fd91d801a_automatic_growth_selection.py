"""Distinguish automatic growth selection from an explicit human choice.

Revision ID: 2c4fd91d801a
Revises: f601a7f91551
"""

from alembic import op
import sqlalchemy as sa

revision = "2c4fd91d801a"
down_revision = "f601a7f91551"
branch_labels = None
depends_on = None


def upgrade():
    # ADD COLUMN preserves the existing people/faces/milestones without rebuilding
    # the parent table (SQLite foreign-key cascades must not delete those rows).
    op.add_column("people", sa.Column("growth_mode", sa.String(10), nullable=False, server_default="auto"))
    op.execute("UPDATE people SET growth_mode = 'include' WHERE growth_enabled = 1")


def downgrade():
    op.drop_column("people", "growth_mode")
