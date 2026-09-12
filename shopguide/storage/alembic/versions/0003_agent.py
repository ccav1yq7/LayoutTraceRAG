"""Durable sessions, runs, events and simulated tool execution."""

import sqlalchemy as sa
from alembic import op

revision = "sg0003"
down_revision = "sg0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sg_sessions",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("principal", sa.String, nullable=False),
        sa.Column("domain", sa.String, nullable=False),
        sa.Column("snapshot", sa.String, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("task", sa.String, nullable=False),
        sa.Column("product", sa.String),
        sa.Column("variant", sa.String),
        sa.Column("active_run", sa.String),
    )
    op.create_table(
        "sg_runs",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("session", sa.String, nullable=False),
        sa.Column("task", sa.String, nullable=False),
        sa.Column("client_message", sa.String, nullable=False),
        sa.Column("message_hash", sa.String, nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("cancelled", sa.Boolean, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("result", sa.Text),
        sa.UniqueConstraint("session", "client_message", name="sg_message_unique"),
    )
    op.create_table(
        "sg_events",
        sa.Column("run", sa.String, primary_key=True),
        sa.Column("seq", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
    )
    op.create_table(
        "sg_tool_calls",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("run", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("arguments_hash", sa.String, nullable=False),
        sa.Column("state", sa.String, nullable=False),
        sa.Column("result", sa.Text),
        sa.Column("side_effect", sa.Boolean, nullable=False),
        sa.UniqueConstraint("run", "name", "arguments_hash", name="sg_tool_unique"),
    )
    op.create_table(
        "sg_confirmations",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("run", sa.String, nullable=False),
        sa.Column("session", sa.String, nullable=False),
        sa.Column("task", sa.String, nullable=False),
        sa.Column("principal", sa.String, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("arguments_hash", sa.String, nullable=False),
        sa.Column("arguments", sa.Text, nullable=False),
        sa.Column("expires", sa.Float, nullable=False),
        sa.Column("approved", sa.Boolean, nullable=False),
        sa.UniqueConstraint("run", "arguments_hash", name="sg_confirmation_unique"),
    )
    op.create_table(
        "sg_tickets",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("confirmation", sa.String, nullable=False, unique=True),
        sa.Column("principal", sa.String, nullable=False),
        sa.Column("arguments", sa.Text, nullable=False),
    )


def downgrade():
    for table in (
        "sg_tickets",
        "sg_confirmations",
        "sg_tool_calls",
        "sg_events",
        "sg_runs",
        "sg_sessions",
    ):
        op.drop_table(table)
