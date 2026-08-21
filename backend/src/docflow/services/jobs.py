from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from docflow.db.models import (
    AuditEvent,
    IngestionJob,
    JobStatus,
    ReviewTask,
    SourceFile,
    new_id,
)
from docflow.domain.config import RuntimeConfigBundleV1
from docflow.domain.jobs import IngestionJobCreate
from docflow.services.config_service import get_current_config


class JobStateError(RuntimeError):
    pass


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    JobStatus.QUEUED.value: {JobStatus.RUNNING.value, JobStatus.CANCELED.value},
    JobStatus.RUNNING.value: {
        JobStatus.WAITING_COST_CONFIRMATION.value,
        JobStatus.WAITING_REVIEW.value,
        JobStatus.SUCCEEDED.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELED.value,
    },
    JobStatus.WAITING_COST_CONFIRMATION.value: {
        JobStatus.QUEUED.value,
        JobStatus.CANCELED.value,
    },
    JobStatus.WAITING_REVIEW.value: {
        JobStatus.QUEUED.value,
        JobStatus.SUCCEEDED.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELED.value,
    },
    JobStatus.SUCCEEDED.value: set(),
    JobStatus.FAILED.value: {JobStatus.QUEUED.value},
    JobStatus.CANCELED.value: set(),
}


def _model_signatures(config: RuntimeConfigBundleV1) -> dict[str, str]:
    profiles = {profile.profile_id: profile for profile in config.models}
    routed = (
        config.routing.structure_parser,
        config.routing.ocr_primary,
        config.routing.vlm_primary,
        config.routing.visual_retrieval_primary,
        config.routing.text_embedding_primary,
        config.routing.reranker_primary,
        config.routing.qa_generation_primary,
    )
    signatures: dict[str, str] = {}
    for profile_id in routed:
        if not profile_id:
            continue
        profile = profiles.get(profile_id)
        if profile and profile.enabled:
            signatures[profile.capability.value] = profile.model_signature
    return signatures


def normalize_source_roots(values: list[str]) -> list[str]:
    """Resolve, validate and collapse duplicate/nested source directories."""
    roots: list[Path] = []
    for value in values:
        root = Path(value).expanduser().resolve()
        if not root.exists():
            raise JobStateError(f"数据源目录不存在：{root}")
        if not root.is_dir():
            raise JobStateError(f"数据源路径不是目录：{root}")
        # A selected parent already covers this root.
        if any(root == existing or existing in root.parents for existing in roots):
            continue
        # If the parent is selected later, discard its previously selected children.
        roots = [existing for existing in roots if root not in existing.parents]
        roots.append(root)
    if not roots:
        raise JobStateError("至少选择一个有效的数据源目录")
    return [str(root) for root in roots]


def job_source_roots(job: IngestionJob) -> list[Path]:
    raw_roots = job.options.get("source_roots") if isinstance(job.options, dict) else None
    values = raw_roots if isinstance(raw_roots, list) and raw_roots else [job.source_root]
    return [Path(str(value)).expanduser().resolve() for value in values]


def create_job(db: Session, payload: IngestionJobCreate) -> IngestionJob:
    config_version = get_current_config(db)
    config = RuntimeConfigBundleV1.model_validate(config_version.content)
    source_roots = normalize_source_roots(payload.source_roots)
    if payload.options.cloud_processing_allowed and not config.budget.cloud_processing_allowed:
        raise JobStateError("当前配置禁止云端处理")
    if (
        payload.job_type == "FULL_SCAN"
        and payload.options.cloud_processing_allowed
        and config.budget.full_run_requires_confirmation
        and not payload.options.full_cloud_run_confirmed
    ):
        raise JobStateError("全量云端任务必须显式确认费用")
    options = payload.options.model_dump(mode="json")
    options["source_roots"] = source_roots
    job = IngestionJob(
        job_type=payload.job_type,
        source_root=source_roots[0],
        config_version_id=config_version.id,
        index_generation_id=new_id("idx"),
        options=options,
        progress={"total": 0, "completed": 0, "running": 0, "failed": 0, "waiting_review": 0},
        stage_counts={},
        model_signatures=_model_signatures(config),
        cloud_usage={"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_cny": 0.0},
    )
    db.add(job)
    db.flush()
    db.add(
        AuditEvent(
            event_type="INGESTION_JOB_CREATED",
            target_type="ingestion_job",
            target_id=job.id,
            details={
                "job_type": job.job_type,
                "config_version_id": job.config_version_id,
                "source_roots": source_roots,
            },
        )
    )
    db.commit()
    db.refresh(job)
    return job


def transition_job(
    db: Session,
    job: IngestionJob,
    status: JobStatus,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
) -> IngestionJob:
    if status.value not in ALLOWED_TRANSITIONS.get(job.status, set()):
        raise JobStateError(f"非法任务状态转换：{job.status} -> {status.value}")
    now = datetime.now(UTC)
    job.status = status.value
    if status == JobStatus.RUNNING and job.started_at is None:
        job.started_at = now
    if status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELED}:
        job.finished_at = now
    job.error_code = error_code
    job.error_message = error_message
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def refresh_job_counts(db: Session, job: IngestionJob) -> IngestionJob:
    rows = db.execute(
        select(SourceFile.status, func.count(SourceFile.id))
        .where(SourceFile.job_id == job.id)
        .group_by(SourceFile.status)
    ).all()
    stage_counts = {status: count for status, count in rows}
    total = sum(stage_counts.values())
    terminal_names = {
        "DUPLICATE",
        "SKIPPED_TEMP",
        "UNSUPPORTED",
        "PARSED",
        "INDEXED",
        "PUBLISHED",
        "FAILED",
    }
    completed = (
        total
        if job.options.get("inventory_only")
        else sum(count for status, count in rows if status in terminal_names)
    )
    job.stage_counts = stage_counts
    job.progress = {
        "total": total,
        "completed": completed,
        "running": stage_counts.get("PROCESSING", 0),
        "failed": stage_counts.get("FAILED", 0),
        "waiting_review": stage_counts.get("WAITING_REVIEW", 0),
    }
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def retry_job(db: Session, job: IngestionJob) -> IngestionJob:
    if job.status not in {
        JobStatus.FAILED.value,
        JobStatus.WAITING_REVIEW.value,
        JobStatus.WAITING_COST_CONFIRMATION.value,
    }:
        raise JobStateError("只有失败、待审核或待费用确认的任务可以重试")
    return transition_job(db, job, JobStatus.QUEUED)


def cancel_job(db: Session, job: IngestionJob) -> IngestionJob:
    if JobStatus.CANCELED.value not in ALLOWED_TRANSITIONS.get(job.status, set()):
        raise JobStateError("当前状态不能取消")
    return transition_job(db, job, JobStatus.CANCELED)


def list_review_tasks(db: Session, status: str = "OPEN", limit: int = 100) -> list[ReviewTask]:
    return list(
        db.scalars(
            select(ReviewTask)
            .where(ReviewTask.status == status)
            .order_by(ReviewTask.created_at.desc())
            .limit(limit)
        )
    )
