from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from docflow.db.session import get_db
from docflow.domain.golden import GoldenAnnotationUpdate, GoldenReviewRequest
from docflow.services.golden import (
    get_golden_sample,
    golden_detail,
    golden_report,
    replace_golden_sample,
    review_golden_sample,
    save_golden_annotation,
)
from docflow.services.golden_preview import GoldenPreviewError, ensure_golden_preview

router = APIRouter(prefix="/admin/golden-set", tags=["golden-set"])


@router.get("")
def list_golden_samples(
    category: str | None = None,
    status: str | None = None,
    search: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
):
    report = golden_report(db)
    items = report["samples"]
    if category:
        items = [item for item in items if item["category"] == category]
    if status:
        items = [item for item in items if item["annotation"]["status"] == status]
    if search:
        keyword = search.strip().casefold()
        items = [
            item
            for item in items
            if keyword in item["source"]["relative_path"].casefold()
            or keyword in item["id"].casefold()
        ]
    report["samples"] = items
    report["filtered_total"] = len(items)
    return report


@router.post("/export")
def export_golden_samples(db: Session = Depends(get_db)):
    return golden_report(db)


@router.get("/{sample_id}")
def get_golden_sample_detail(sample_id: str, db: Session = Depends(get_db)):
    try:
        return golden_detail(db, sample_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{sample_id}/annotation")
def update_golden_annotation(
    sample_id: str,
    payload: GoldenAnnotationUpdate,
    db: Session = Depends(get_db),
):
    try:
        return save_golden_annotation(db, sample_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{sample_id}/approve")
def approve_golden_sample(
    sample_id: str,
    payload: GoldenReviewRequest,
    db: Session = Depends(get_db),
):
    try:
        return review_golden_sample(db, sample_id, "approve", payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{sample_id}/reject")
def reject_golden_sample(
    sample_id: str,
    payload: GoldenReviewRequest,
    db: Session = Depends(get_db),
):
    try:
        return review_golden_sample(db, sample_id, "reject", payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{sample_id}/replace")
def replace_golden_candidate(
    sample_id: str,
    payload: GoldenReviewRequest,
    db: Session = Depends(get_db),
):
    try:
        return replace_golden_sample(db, sample_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{sample_id}/preview")
def golden_sample_preview(sample_id: str, db: Session = Depends(get_db)):
    try:
        sample, source = get_golden_sample(db, sample_id)
        preview = ensure_golden_preview(source, sample.page_number)
        return FileResponse(preview, media_type="image/png")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GoldenPreviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{sample_id}/source")
def golden_sample_source(sample_id: str, db: Session = Depends(get_db)):
    try:
        _, source = get_golden_sample(db, sample_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    path = Path(source.source_path).expanduser().resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="源文件不存在")
    return FileResponse(path, filename=source.file_name, content_disposition_type="inline")
