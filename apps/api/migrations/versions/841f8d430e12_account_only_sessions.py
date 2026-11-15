"""Allow authenticated accounts without a current household.

Revision ID: 841f8d430e12
Revises: 2c4fd91d801a
"""

from alembic import op
import sqlalchemy as sa

revision = "841f8d430e12"
down_revision = "2c4fd91d801a"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("sessions") as batch:
        batch.alter_column("household_id", existing_type=sa.String(32), nullable=True)


def downgrade():
    # These sessions have no representation in the old schema; accounts remain.
    op.execute("DELETE FROM sessions WHERE household_id IS NULL")
    with op.batch_alter_table("sessions") as batch:
        batch.alter_column("household_id", existing_type=sa.String(32), nullable=False)
