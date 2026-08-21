from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from docflow.db.models import AuditEvent, ReviewTask
from docflow.db.session import get_db
from docflow.domain.jobs import ReviewResolveRequest
from docflow.services.jobs import list_review_tasks

router = APIRouter(prefix="/admin/review-tasks", tags=["review"])


def _review_response(task: ReviewTask) -> dict:
    return {
        "id": task.id,
        "job_id": task.job_id,
        "source_file_id": task.source_file_id,
        "document_id": task.document_id,
        "category": task.category,
        "severity": task.severity,
        "status": task.status,
        "summary": task.summary,
        "details": task.details,
        "resolution": task.resolution,
        "created_at": task.created_at,
        "resolved_at": task.resolved_at,
    }


@router.get("")
def reviews(
    status: str = "OPEN",
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return [_review_response(task) for task in list_review_tasks(db, status, limit)]


@router.get("/{task_id}")
def review(task_id: str, db: Session = Depends(get_db)):
    task = db.get(ReviewTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="审核任务不存在")
    return _review_response(task)


@router.post("/{task_id}/resolve")
def resolve_review(task_id: str, payload: ReviewResolveRequest, db: Session = Depends(get_db)):
    task = db.get(ReviewTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="审核任务不存在")
    if task.status != "OPEN":
        raise HTTPException(status_code=409, detail="审核任务已处理")
    task.status = "RESOLVED"
    task.resolved_at = datetime.now(UTC)
    task.resolution = payload.model_dump(mode="json")
    db.add(task)
    db.add(
        AuditEvent(
            event_type="REVIEW_RESOLVED",
            target_type="review_task",
            target_id=task.id,
            details={"action": payload.action, "reason": payload.reason},
        )
    )
    db.commit()
    db.refresh(task)
    return _review_response(task)
