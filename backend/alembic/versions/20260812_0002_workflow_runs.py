"""Add auditable LangGraph workflow runs.

Revision ID: 20260812_0002
Revises: 20260811_0001
"""

import sqlalchemy as sa
from alembic import op


revision = "20260812_0002"
down_revision = "20260811_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("workflow_runs"):
        return
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workflow_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("config_version_id", sa.String(length=64), nullable=True),
        sa.Column("index_generation_id", sa.String(length=96), nullable=True),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("trace_json", sa.JSON(), nullable=False),
        sa.Column("engine", sa.String(length=64), nullable=False),
        sa.Column("engine_version", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_runs_workflow_type", "workflow_runs", ["workflow_type"])
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])
    op.create_index("ix_workflow_runs_config_version_id", "workflow_runs", ["config_version_id"])
    op.create_index(
        "ix_workflow_runs_index_generation_id",
        "workflow_runs",
        ["index_generation_id"],
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("workflow_runs"):
        op.drop_table("workflow_runs")
