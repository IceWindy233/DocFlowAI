from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class Base(DeclarativeBase):
    pass


class JobStatus(enum.StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_COST_CONFIRMATION = "WAITING_COST_CONFIRMATION"
    WAITING_REVIEW = "WAITING_REVIEW"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class SourceStatus(enum.StrEnum):
    DISCOVERED = "DISCOVERED"
    READY = "READY"
    DUPLICATE = "DUPLICATE"
    SKIPPED_TEMP = "SKIPPED_TEMP"
    UNSUPPORTED = "UNSUPPORTED"
    PROCESSING = "PROCESSING"
    PARSED = "PARSED"
    INDEXED = "INDEXED"
    WAITING_REVIEW = "WAITING_REVIEW"
    FAILED = "FAILED"
    PUBLISHED = "PUBLISHED"


class ReviewStatus(enum.StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class ConfigVersion(Base):
    __tablename__ = "config_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cfg"))
    version: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    impact: Mapped[str] = mapped_column(String(32), default="HOT")
    impact_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), default="local-admin")
    change_reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("job"))
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    source_root: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default=JobStatus.QUEUED.value, index=True)
    config_version_id: Mapped[str] = mapped_column(ForeignKey("config_versions.id"), index=True)
    index_generation_id: Mapped[str] = mapped_column(String(96))
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stage_counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model_signatures: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cloud_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    config_version: Mapped[ConfigVersion] = relationship()


class SourceFile(Base):
    __tablename__ = "source_files"
    __table_args__ = (
        Index("ix_source_files_sha", "sha256"),
        Index("ix_source_files_job_status", "job_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("src"))
    job_id: Mapped[str] = mapped_column(ForeignKey("ingestion_jobs.id"), index=True)
    source_path: Mapped[str] = mapped_column(Text)
    relative_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(Text)
    extension: Mapped[str] = mapped_column(String(32), default="")
    mime_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonical_source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default=SourceStatus.DISCOVERED.value)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    archive_entries: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[IngestionJob] = relationship()


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("doc"))
    source_file_id: Mapped[str] = mapped_column(ForeignKey("source_files.id"), unique=True)
    config_version_id: Mapped[str] = mapped_column(ForeignKey("config_versions.id"), index=True)
    case_id: Mapped[str] = mapped_column(String(96), index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    document_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    document_role: Mapped[str] = mapped_column(String(64), default="UNKNOWN")
    version_role: Mapped[str] = mapped_column(String(64), default="UNKNOWN")
    authority_score: Mapped[float] = mapped_column(Float, default=0.0)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    parser_route: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(128))
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    normalized: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source_file: Mapped[SourceFile] = relationship()
    pages: Mapped[list[Page]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (Index("ix_pages_document_number", "document_id", "page_number", unique=True),)

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    page_type: Mapped[str] = mapped_column(String(64), default="TEXT")
    text: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    visual_required: Mapped[bool] = mapped_column(Boolean, default=False)
    visual_status: Mapped[str] = mapped_column(String(32), default="NOT_REQUIRED")
    visual_model_signature: Mapped[str | None] = mapped_column(String(200), nullable=True)

    document: Mapped[Document] = relationship(back_populates="pages")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    page_id: Mapped[str] = mapped_column(ForeignKey("pages.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32), default="paragraph")
    text: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    embedding_status: Mapped[str] = mapped_column(String(32), default="PENDING")
    embedding_signature: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ReviewTask(Base):
    __tablename__ = "review_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("review"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_jobs.id"), nullable=True)
    source_file_id: Mapped[str | None] = mapped_column(ForeignKey("source_files.id"), nullable=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(24), default="WARNING")
    status: Mapped[str] = mapped_column(String(24), default=ReviewStatus.OPEN.value, index=True)
    summary: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("pub"))
    config_version_id: Mapped[str] = mapped_column(ForeignKey("config_versions.id"), index=True)
    index_generation_id: Mapped[str] = mapped_column(String(96), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelProbe(Base):
    __tablename__ = "model_probes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("probe"))
    profile_id: Mapped[str] = mapped_column(String(128), index=True)
    config_version_id: Mapped[str] = mapped_column(ForeignKey("config_versions.id"))
    success: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capability_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("run"))
    workflow_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING", index=True)
    config_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    index_generation_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trace_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    engine: Mapped[str] = mapped_column(String(64), default="langgraph")
    engine_version: Mapped[str] = mapped_column(String(32), default="unknown")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QaEvaluationSample(Base):
    __tablename__ = "qa_evaluation_samples"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("qae"))
    index_generation_id: Mapped[str] = mapped_column(String(96), index=True)
    question: Mapped[str] = mapped_column(Text)
    reference_answer: Mapped[str] = mapped_column(Text)
    answer_aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    expected_page_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    expected_document_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    category: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", index=True)
    source: Mapped[str] = mapped_column(String(24), default="AUTO")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QaEvaluationRun(Base):
    __tablename__ = "qa_evaluation_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("qarun"))
    index_generation_id: Mapped[str] = mapped_column(String(96), index=True)
    config_version_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="RUNNING", index=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    cloud_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentEvaluationRun(Base):
    __tablename__ = "agent_evaluation_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("aerun"))
    sample_set_id: Mapped[str] = mapped_column(String(96), index=True)
    capability: Mapped[str] = mapped_column(String(24), index=True)
    mode: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(24), default="RUNNING", index=True)
    config_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    index_generation_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    cloud_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentReview(Base):
    __tablename__ = "document_reviews"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("rvw"))
    document_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    input_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING", index=True)
    scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    revised_text: Mapped[str] = mapped_column(Text, default="")
    report_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_version_id: Mapped[str] = mapped_column(String(64), index=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model_signature: Mapped[str] = mapped_column(String(240), default="")
    cloud_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewFinding(Base):
    __tablename__ = "review_findings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("finding"))
    review_id: Mapped[str] = mapped_column(ForeignKey("document_reviews.id"), index=True)
    severity: Mapped[str] = mapped_column(String(24), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    location: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    original_text: Mapped[str] = mapped_column(Text, default="")
    suggested_text: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    sources: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    auto_fixable: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    feedback: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DraftTask(Base):
    __tablename__ = "draft_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("draft"))
    document_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="PLANNING", index=True)
    requirements: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    selected_cases: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    evidence_bundle: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    outline: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    draft_text: Mapped[str] = mapped_column(Text, default="")
    verification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    export_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_version_id: Mapped[str] = mapped_column(String(64), index=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model_signature: Mapped[str] = mapped_column(String(240), default="")
    cloud_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DraftRevision(Base):
    __tablename__ = "draft_revisions"
    __table_args__ = (
        Index("ix_draft_revisions_number", "draft_id", "revision_number", unique=True),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("revision")
    )
    draft_id: Mapped[str] = mapped_column(ForeignKey("draft_tasks.id"), index=True)
    revision_number: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32), index=True)
    draft_text: Mapped[str] = mapped_column(Text)
    verification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model_signature: Mapped[str] = mapped_column(String(240), default="")
    cloud_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("audit"))
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    actor: Mapped[str] = mapped_column(String(128), default="local-admin")
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(128))
    before_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GoldenSample(Base):
    __tablename__ = "golden_samples"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("golden"))
    source_file_id: Mapped[str] = mapped_column(ForeignKey("source_files.id"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(64), index=True)
    selection_reason: Mapped[str] = mapped_column(Text)
    annotation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
