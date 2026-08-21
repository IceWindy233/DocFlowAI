"""Add review provenance and draft revision history.

Revision ID: 20260813_0005
Revises: 20260813_0004
"""

import sqlalchemy as sa
from alembic import op


revision = "20260813_0005"
down_revision = "20260813_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    finding_columns = {item["name"] for item in inspector.get_columns("review_findings")}
    if "sources" not in finding_columns:
        op.add_column(
            "review_findings",
            sa.Column("sources", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        )
    if not inspector.has_table("draft_revisions"):
        op.create_table(
            "draft_revisions",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("draft_id", sa.String(64), sa.ForeignKey("draft_tasks.id"), nullable=False),
            sa.Column("revision_number", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(32), nullable=False),
            sa.Column("draft_text", sa.Text(), nullable=False),
            sa.Column("verification", sa.JSON(), nullable=False),
            sa.Column("model_signature", sa.String(240), nullable=False),
            sa.Column("cloud_usage", sa.JSON(), nullable=False),
            sa.Column("note", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_draft_revisions_draft_id", "draft_revisions", ["draft_id"])
        op.create_index("ix_draft_revisions_source", "draft_revisions", ["source"])
        op.create_index(
            "ix_draft_revisions_number",
            "draft_revisions",
            ["draft_id", "revision_number"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("draft_revisions"):
        op.drop_table("draft_revisions")
    finding_columns = {item["name"] for item in inspector.get_columns("review_findings")}
    if "sources" in finding_columns:
        op.drop_column("review_findings", "sources")
