"""Snapshot publication and integrity manifests, separate from immutable records."""

import sqlalchemy as sa
from alembic import op

revision = "sg0002"
down_revision = "sg0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sg_snapshots",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("domain", sa.String, nullable=False),
        sa.Column("identity", sa.Text, nullable=False),
        sa.Column("state", sa.String, nullable=False),
        sa.Column("manifest", sa.Text),
        sa.Column("error", sa.String),
    )
    op.create_table(
        "sg_active",
        sa.Column("domain", sa.String, primary_key=True),
        sa.Column("snapshot", sa.String, nullable=False),
    )


def downgrade():
    op.drop_table("sg_active")
    op.drop_table("sg_snapshots")
