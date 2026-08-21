from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from docflow.db.models import AuditEvent, Document, Page, SourceFile
from docflow.db.session import get_db
from docflow.domain.jobs import (
    DocumentCorrectionRequest,
    IngestionJobCreate,
    IngestionOptions,
    ReparseRequest,
)
from docflow.services.jobs import create_job
from docflow.services.storage import LocalArtifactStore

router = APIRouter(prefix="/admin/documents", tags=["documents"])


def _document_summary(document: Document) -> dict:
    return {
        "id": document.id,
        "source_file_id": document.source_file_id,
        "config_version_id": document.config_version_id,
        "case_id": document.case_id,
        "title": document.title,
        "document_number": document.document_number,
        "document_role": document.document_role,
        "version_role": document.version_role,
        "authority_score": document.authority_score,
        "selected": document.selected,
        "parser_route": document.parser_route,
        "parser_version": document.parser_version,
        "quality_score": document.quality_score,
        "created_at": document.created_at,
    }


@router.get("")
def documents(
    case_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    query = select(Document)
    if case_id:
        query = query.where(Document.case_id == case_id)
    items = db.scalars(query.order_by(Document.created_at.desc()).offset(offset).limit(limit))
    return [_document_summary(item) for item in items]


@router.get("/{document_id}")
def document_detail(document_id: str, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    source = db.get(SourceFile, document.source_file_id)
    pages = list(
        db.scalars(select(Page).where(Page.document_id == document_id).order_by(Page.page_number))
    )
    return {
        **_document_summary(document),
        "source": {
            "relative_path": source.relative_path,
            "file_name": source.file_name,
            "sha256": source.sha256,
            "status": source.status,
        },
        "normalized": document.normalized,
        "pages": [
            {
                "id": page.id,
                "page_number": page.page_number,
                "page_type": page.page_type,
                "text": page.text,
                "content": page.content,
                "quality_score": page.quality_score,
                "image_path": (
                    f"/api/v1/artifacts/{document.id}/pages/{Path(page.image_path).name}"
                    if page.image_path and document.id in page.image_path
                    else None
                ),
                "visual_required": page.visual_required,
                "visual_status": page.visual_status,
            }
            for page in pages
        ],
    }


@router.patch("/{document_id}")
def correct_document(
    document_id: str,
    payload: DocumentCorrectionRequest,
    db: Session = Depends(get_db),
):
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    before = _document_summary(document)
    changes = payload.model_dump(exclude_none=True, exclude={"reason"})
    target_case_id = str(changes.get("case_id", document.case_id))
    target_role = str(changes.get("document_role", document.document_role))
    effective_selected = bool(changes.get("selected", document.selected))
    if effective_selected:
        # A case may contain several authoritative roles (letter, attachment, reply),
        # but only one authoritative version for the same role.
        db.execute(
            update(Document)
            .where(
                Document.id != document.id,
                Document.case_id == target_case_id,
                Document.document_role == target_role,
                Document.selected.is_(True),
            )
            .values(selected=False)
        )
    for field, value in changes.items():
        setattr(document, field, value)
    normalized = dict(document.normalized or {})
    for field in (
        "title",
        "document_number",
        "document_role",
        "version_role",
        "case_id",
    ):
        if field in changes:
            normalized[field] = changes[field]
    document.normalized = normalized
    db.add(document)
    db.add(
        AuditEvent(
            event_type="DOCUMENT_CORRECTED",
            target_type="document",
            target_id=document.id,
            details=jsonable_encoder(
                {"before": before, "changes": changes, "reason": payload.reason}
            ),
        )
    )
    db.commit()
    db.refresh(document)
    LocalArtifactStore().write_json(
        f"{document.id}/normalized-document.json", document.normalized
    )
    return _document_summary(document)


@router.post("/{document_id}/reparse")
def reparse_document(
    document_id: str,
    payload: ReparseRequest,
    db: Session = Depends(get_db),
):
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    source = db.get(SourceFile, document.source_file_id)
    job = create_job(
        db,
        IngestionJobCreate(
            job_type="INCREMENTAL_SCAN",
            source_root=str(Path(source.source_path).parent),
            options=IngestionOptions(
                force_reparse=True,
                cloud_processing_allowed=payload.cloud_processing_allowed,
                full_cloud_run_confirmed=payload.full_cloud_run_confirmed,
            ),
        ),
    )
    cloned = SourceFile(
        job_id=job.id,
        source_path=source.source_path,
        relative_path=source.file_name,
        file_name=source.file_name,
        extension=source.extension,
        mime_type=source.mime_type,
        size_bytes=source.size_bytes,
        modified_at=source.modified_at,
        sha256=source.sha256,
        status="READY",
        case_hint=source.case_hint,
        page_count=source.page_count,
    )
    db.add(cloned)
    db.add(
        AuditEvent(
            event_type="DOCUMENT_REPARSE_REQUESTED",
            target_type="document",
            target_id=document.id,
            details={"reason": payload.reason, "job_id": job.id},
        )
    )
    db.commit()
    return {"job_id": job.id, "status": job.status, "config_version_id": job.config_version_id}
