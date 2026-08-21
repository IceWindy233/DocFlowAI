from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from docflow.db.models import IngestionJob, Publication
from docflow.db.session import get_db
from docflow.services.publication import (
    PublicationValidationError,
    create_and_publish,
    validate_publication,
)

router = APIRouter(prefix="/admin/publications", tags=["publication"])


@router.get("")
def publications(db: Session = Depends(get_db)):
    items = db.scalars(select(Publication).order_by(Publication.created_at.desc()).limit(100))
    return [
        {
            "id": item.id,
            "config_version_id": item.config_version_id,
            "index_generation_id": item.index_generation_id,
            "status": item.status,
            "active": item.active,
            "validation": item.validation,
            "created_at": item.created_at,
            "published_at": item.published_at,
        }
        for item in items
    ]


@router.post("/validate/{job_id}")
def validate(job_id: str, db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return validate_publication(db, job.config_version_id, job.index_generation_id)


@router.post("/publish/{job_id}")
def publish(job_id: str, db: Session = Depends(get_db)):
    job = db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        item = create_and_publish(db, job.config_version_id, job.index_generation_id)
        return {
            "id": item.id,
            "status": item.status,
            "active": item.active,
            "validation": item.validation,
        }
    except PublicationValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.validation) from exc


@router.post("/{publication_id}/activate")
def activate(publication_id: str, db: Session = Depends(get_db)):
    publication = db.get(Publication, publication_id)
    if not publication:
        raise HTTPException(status_code=404, detail="Publication 不存在")
    try:
        item = create_and_publish(
            db,
            publication.config_version_id,
            publication.index_generation_id,
        )
        return {
            "id": item.id,
            "status": item.status,
            "active": item.active,
            "validation": item.validation,
        }
    except PublicationValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.validation) from exc
