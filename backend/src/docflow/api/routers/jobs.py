from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from docflow.core.settings import get_settings
from docflow.db.models import IngestionJob, SourceFile
from docflow.db.session import SessionLocal, get_db
from docflow.domain.jobs import IngestionJobCreate, IngestionJobResponse
from docflow.services.golden import golden_report, select_golden_set
from docflow.services.inventory import build_inventory_report
from docflow.services.jobs import JobStateError, cancel_job, create_job, retry_job
from docflow.services.native_directory_picker import (
    NativeDirectoryPickerError,
    pick_source_directories,
)
from docflow.services.reports import benchmark_report
from docflow.services.source_browser import SourceDirectoryError, browse_source_directories

router = APIRouter(prefix="/admin/ingestion", tags=["ingestion"])


@router.get("/source-directories")
def source_directories(path: str | None = None):
    try:
        return browse_source_directories(get_settings().source_root, path)
    except SourceDirectoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/source-directories/pick")
def native_source_directories():
    """Open the native macOS Finder picker on this localhost-only M1 service."""
    try:
        return pick_source_directories()
    except NativeDirectoryPickerError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc


def _run_job_inline(job_id: str) -> None:
    from docflow.services.pipeline import process_job

    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        if job:
            process_job(db, job)


def _dispatch(job_id: str, background: BackgroundTasks) -> None:
    if get_settings().execution_mode == "celery":
        from docflow.workers.tasks import run_ingestion_job

        run_ingestion_job.delay(job_id)
    else:
        background.add_task(_run_job_inline, job_id)


@router.post("/jobs", response_model=IngestionJobResponse, status_code=201)
def create_ingestion_job(payload: IngestionJobCreate, db: Session = Depends(get_db)):
    try:
        return create_job(db, payload)
    except JobStateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/jobs", response_model=list[IngestionJobResponse])
def ingestion_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return list(
        db.scalars(select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(limit))
    )


@router.get("/jobs/{job_id}", response_model=IngestionJobResponse)
def ingestion_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@router.post("/jobs/{job_id}/run", response_model=IngestionJobResponse)
def run_job(job_id: str, background: BackgroundTasks, db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status != "QUEUED":
        raise HTTPException(status_code=409, detail="只有排队中的任务可以启动")
    _dispatch(job.id, background)
    return job


@router.post("/jobs/{job_id}/retry", response_model=IngestionJobResponse)
def retry(job_id: str, background: BackgroundTasks, db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        job = retry_job(db, job)
        _dispatch(job.id, background)
        return job
    except JobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cancel", response_model=IngestionJobResponse)
def cancel(job_id: str, db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        return cancel_job(db, job)
    except JobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/files")
def job_files(
    job_id: str,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    query = select(SourceFile).where(SourceFile.job_id == job_id)
    if status:
        query = query.where(SourceFile.status == status)
    items = list(db.scalars(query.order_by(SourceFile.relative_path).offset(offset).limit(limit)))
    return [
        {
            "id": item.id,
            "relative_path": item.relative_path,
            "extension": item.extension,
            "mime_type": item.mime_type,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "status": item.status,
            "status_reason": item.status_reason,
            "case_hint": item.case_hint,
            "page_count": item.page_count,
        }
        for item in items
    ]


@router.get("/jobs/{job_id}/inventory-report")
def inventory_report(job_id: str, db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return build_inventory_report(db, job)


@router.post("/jobs/{job_id}/golden-set")
def create_golden_set(job_id: str, replace: bool = False, db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return select_golden_set(db, job, replace=replace)


@router.get("/golden-set")
def get_golden_set(db: Session = Depends(get_db)):
    return golden_report(db)


@router.get("/jobs/{job_id}/benchmark-report")
def get_benchmark_report(job_id: str, db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return benchmark_report(db, job)
