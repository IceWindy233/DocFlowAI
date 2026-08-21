"""Add generic Agent evaluation runs.

Revision ID: 20260816_0006
Revises: 20260813_0005
"""

import sqlalchemy as sa

from alembic import op

revision = "20260816_0006"
down_revision = "20260813_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("agent_evaluation_runs"):
        return
    op.create_table(
        "agent_evaluation_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("sample_set_id", sa.String(96), nullable=False),
        sa.Column("capability", sa.String(24), nullable=False),
        sa.Column("mode", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("config_version_id", sa.String(64), nullable=True),
        sa.Column("index_generation_id", sa.String(96), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("cloud_usage", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in [
        "sample_set_id",
        "capability",
        "mode",
        "status",
        "config_version_id",
        "index_generation_id",
    ]:
        op.create_index(f"ix_agent_evaluation_runs_{column}", "agent_evaluation_runs", [column])


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("agent_evaluation_runs"):
        op.drop_table("agent_evaluation_runs")
