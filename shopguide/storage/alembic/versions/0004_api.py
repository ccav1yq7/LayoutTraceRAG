"""HTTP authentication, task-bound private uploads and feedback."""

import sqlalchemy as sa
from alembic import op

revision = "sg0004"
down_revision = "sg0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sg_http_auth",
        sa.Column("token_hash", sa.String, primary_key=True),
        sa.Column("principal", sa.String, nullable=False),
        sa.Column("csrf", sa.String, nullable=False),
        sa.Column("expires", sa.Float, nullable=False),
    )
    op.create_table(
        "sg_uploads",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("principal", sa.String, nullable=False),
        sa.Column("session", sa.String, nullable=False),
        sa.Column("task", sa.String, nullable=False),
        sa.Column("original_sha256", sa.String, nullable=False),
        sa.Column("sha256", sa.String, nullable=False),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("expires", sa.Float, nullable=False),
    )
    op.create_table(
        "sg_feedback",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("run", sa.String, nullable=False),
        sa.Column("principal", sa.String, nullable=False),
        sa.Column("reason", sa.String, nullable=False),
        sa.Column("note", sa.Text, nullable=False),
    )


def downgrade():
    for table in ("sg_feedback", "sg_uploads", "sg_http_auth"):
        op.drop_table(table)
