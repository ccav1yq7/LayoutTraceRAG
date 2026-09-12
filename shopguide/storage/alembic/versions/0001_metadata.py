"""Immutable V2 records and explicit per-principal scope grants."""

import sqlalchemy as sa
from alembic import op

revision = "sg0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sg_records",
        sa.Column("kind", sa.String, primary_key=True),
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("payload", sa.Text, nullable=False),
    )
    op.create_table(
        "sg_grants",
        sa.Column("principal", sa.String, primary_key=True),
        sa.Column("product", sa.String, primary_key=True),
        sa.Column("variant", sa.String, primary_key=True),
        sa.Column("document", sa.String, primary_key=True),
        sa.Column("snapshot", sa.String, primary_key=True),
        sa.Column("domain", sa.String, nullable=False),
        sa.Column("basis", sa.Text, nullable=False),
    )


def downgrade():
    op.drop_table("sg_grants")
    op.drop_table("sg_records")
