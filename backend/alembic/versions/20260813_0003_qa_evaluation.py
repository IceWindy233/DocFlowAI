"""Add lightweight RAG QA evaluation tables.

Revision ID: 20260813_0003
Revises: 20260812_0002
"""

import sqlalchemy as sa
from alembic import op


revision = "20260813_0003"
down_revision = "20260812_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("qa_evaluation_samples"):
        op.create_table(
            "qa_evaluation_samples",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("index_generation_id", sa.String(length=96), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("reference_answer", sa.Text(), nullable=False),
            sa.Column("answer_aliases", sa.JSON(), nullable=False),
            sa.Column("expected_page_ids", sa.JSON(), nullable=False),
            sa.Column("expected_document_ids", sa.JSON(), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("source", sa.String(length=24), nullable=False),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_qa_evaluation_samples_index_generation_id",
            "qa_evaluation_samples",
            ["index_generation_id"],
        )
        op.create_index(
            "ix_qa_evaluation_samples_category",
            "qa_evaluation_samples",
            ["category"],
        )
        op.create_index(
            "ix_qa_evaluation_samples_status",
            "qa_evaluation_samples",
            ["status"],
        )
    if not inspector.has_table("qa_evaluation_runs"):
        op.create_table(
            "qa_evaluation_runs",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("index_generation_id", sa.String(length=96), nullable=False),
            sa.Column("config_version_id", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("metrics", sa.JSON(), nullable=False),
            sa.Column("results", sa.JSON(), nullable=False),
            sa.Column("cloud_usage", sa.JSON(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_qa_evaluation_runs_index_generation_id",
            "qa_evaluation_runs",
            ["index_generation_id"],
        )
        op.create_index(
            "ix_qa_evaluation_runs_config_version_id",
            "qa_evaluation_runs",
            ["config_version_id"],
        )
        op.create_index("ix_qa_evaluation_runs_status", "qa_evaluation_runs", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("qa_evaluation_runs"):
        op.drop_table("qa_evaluation_runs")
    if inspector.has_table("qa_evaluation_samples"):
        op.drop_table("qa_evaluation_samples")
