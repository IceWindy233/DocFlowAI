from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from docflow.db.models import Document, IngestionJob, SourceFile
from docflow.db.session import get_db
from docflow.domain.agents import DocumentReviewCreate, FindingResolveRequest, ReviewApplyRequest
from docflow.services.retrieval import RetrievalContextError, _resolve_context
from docflow.services.review_agent import (
    apply_review,
    create_review,
    list_reviews,
    resolve_finding,
    review_detail,
)

router = APIRouter(prefix="/document-reviews", tags=["document-review-agent"])


@router.post("")
def create(payload: DocumentReviewCreate, db: Session = Depends(get_db)):
    try:
        return create_review(db, payload)
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RetrievalContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("")
def list_items(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return list_reviews(db, limit)


@router.get("/source-documents")
def source_documents(
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List reviewable documents from the active published index only."""
    try:
        context = _resolve_context(db)
    except RetrievalContextError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    items = db.scalars(
        select(Document)
        .join(SourceFile, SourceFile.id == Document.source_file_id)
        .join(IngestionJob, IngestionJob.id == SourceFile.job_id)
        .where(IngestionJob.index_generation_id == context.index_generation_id)
        .order_by(Document.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": item.id,
            "source_file_id": item.source_file_id,
            "config_version_id": item.config_version_id,
            "case_id": item.case_id,
            "title": item.title,
            "document_number": item.document_number,
            "document_role": item.document_role,
            "version_role": item.version_role,
            "authority_score": item.authority_score,
            "selected": item.selected,
            "parser_route": item.parser_route,
            "parser_version": item.parser_version,
            "quality_score": item.quality_score,
            "created_at": item.created_at,
        }
        for item in items
    ]


@router.get("/{review_id}")
def detail(review_id: str, db: Session = Depends(get_db)):
    try:
        return review_detail(db, review_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{review_id}/findings/{finding_id}/resolve")
def resolve(
    review_id: str,
    finding_id: str,
    payload: FindingResolveRequest,
    db: Session = Depends(get_db),
):
    try:
        return resolve_finding(db, review_id, finding_id, payload.action, payload.feedback)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{review_id}/apply")
def apply(review_id: str, payload: ReviewApplyRequest, db: Session = Depends(get_db)):
    try:
        return apply_review(db, review_id, payload.accepted_finding_ids)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
