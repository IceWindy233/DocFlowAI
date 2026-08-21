"""Add document review and drafting agent models.

Revision ID: 20260813_0004
Revises: 20260813_0003
"""

import sqlalchemy as sa
from alembic import op


revision = "20260813_0004"
down_revision = "20260813_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("document_reviews"):
        op.create_table(
            "document_reviews",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("document_id", sa.String(64), nullable=True),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("input_text", sa.Text(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("scope", sa.JSON(), nullable=False),
            sa.Column("summary", sa.JSON(), nullable=False),
            sa.Column("revised_text", sa.Text(), nullable=False),
            sa.Column("report_path", sa.Text(), nullable=True),
            sa.Column("config_version_id", sa.String(64), nullable=False),
            sa.Column("workflow_run_id", sa.String(64), nullable=True),
            sa.Column("model_signature", sa.String(240), nullable=False),
            sa.Column("cloud_usage", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        for column in ["document_id", "status", "config_version_id", "workflow_run_id"]:
            op.create_index(f"ix_document_reviews_{column}", "document_reviews", [column])
    if not inspector.has_table("review_findings"):
        op.create_table(
            "review_findings",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("review_id", sa.String(64), sa.ForeignKey("document_reviews.id"), nullable=False),
            sa.Column("severity", sa.String(24), nullable=False),
            sa.Column("category", sa.String(64), nullable=False),
            sa.Column("location", sa.JSON(), nullable=False),
            sa.Column("original_text", sa.Text(), nullable=False),
            sa.Column("suggested_text", sa.Text(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("auto_fixable", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("feedback", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )
        for column in ["review_id", "severity", "category", "status"]:
            op.create_index(f"ix_review_findings_{column}", "review_findings", [column])
    if not inspector.has_table("draft_tasks"):
        op.create_table(
            "draft_tasks",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("document_type", sa.String(32), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("status", sa.String(40), nullable=False),
            sa.Column("requirements", sa.JSON(), nullable=False),
            sa.Column("missing_fields", sa.JSON(), nullable=False),
            sa.Column("selected_cases", sa.JSON(), nullable=False),
            sa.Column("evidence_bundle", sa.JSON(), nullable=False),
            sa.Column("outline", sa.JSON(), nullable=False),
            sa.Column("draft_text", sa.Text(), nullable=False),
            sa.Column("verification", sa.JSON(), nullable=False),
            sa.Column("export_path", sa.Text(), nullable=True),
            sa.Column("config_version_id", sa.String(64), nullable=False),
            sa.Column("workflow_run_id", sa.String(64), nullable=True),
            sa.Column("model_signature", sa.String(240), nullable=False),
            sa.Column("cloud_usage", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        for column in ["document_type", "status", "config_version_id", "workflow_run_id"]:
            op.create_index(f"ix_draft_tasks_{column}", "draft_tasks", [column])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in ["draft_tasks", "review_findings", "document_reviews"]:
        if inspector.has_table(table):
            op.drop_table(table)
