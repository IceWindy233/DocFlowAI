from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from docflow.db.models import Chunk, Document, IngestionJob, Page, Publication, SourceFile
from docflow.domain.config import RuntimeConfigBundleV1
from docflow.services.config_service import get_version
from docflow.services.vector_index import (
    collection_stats,
    text_collection_name,
    visual_collection_name,
)


class PublicationValidationError(RuntimeError):
    def __init__(self, validation: dict[str, Any]) -> None:
        super().__init__("Publication 完整性校验失败")
        self.validation = validation


def validate_publication(
    db: Session, config_version_id: str, index_generation_id: str
) -> dict[str, Any]:
    version = get_version(db, config_version_id)
    config = RuntimeConfigBundleV1.model_validate(version.content)
    job_filter = IngestionJob.index_generation_id == index_generation_id
    supported = (
        db.scalar(
            select(func.count(SourceFile.id))
            .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
            .where(
                job_filter, SourceFile.status.notin_(["SKIPPED_TEMP", "UNSUPPORTED", "DUPLICATE"])
            )
        )
        or 0
    )
    ready_statuses = ["INDEXED", "PUBLISHED"]
    # Compatibility for jobs created before visual-only pipelines began marking
    # successfully indexed sources as INDEXED. Their DISABLED text chunks are
    # intentional when no text embedding route is configured.
    if config.routing.text_embedding_primary is None:
        ready_statuses.append("PARSED")
    published_ready = (
        db.scalar(
            select(func.count(SourceFile.id))
            .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
            .where(job_filter, SourceFile.status.in_(ready_statuses))
        )
        or 0
    )
    missing_visual = (
        db.scalar(
            select(func.count(Page.id))
            .join(Document, Page.document_id == Document.id)
            .join(SourceFile, Document.source_file_id == SourceFile.id)
            .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
            .where(job_filter, Page.visual_required.is_(True), Page.visual_status != "READY")
        )
        or 0
    )
    failed_embeddings = (
        db.scalar(
            select(func.count(Chunk.id))
            .join(Document, Chunk.document_id == Document.id)
            .join(SourceFile, Document.source_file_id == SourceFile.id)
            .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
            .where(job_filter, Chunk.embedding_status == "FAILED")
        )
        or 0
    )
    incomplete_embeddings = 0
    if config.routing.text_embedding_primary is not None:
        incomplete_embeddings = (
            db.scalar(
                select(func.count(Chunk.id))
                .join(Document, Chunk.document_id == Document.id)
                .join(SourceFile, Document.source_file_id == SourceFile.id)
                .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
                .where(job_filter, Chunk.embedding_status != "READY")
            )
            or 0
        )
    missing_page_alignment = (
        db.scalar(
            select(func.count(Chunk.id))
            .join(Document, Chunk.document_id == Document.id)
            .join(SourceFile, Document.source_file_id == SourceFile.id)
            .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
            .outerjoin(Page, Chunk.page_id == Page.id)
            .where(job_filter, Page.id.is_(None))
        )
        or 0
    )
    document_count = (
        db.scalar(
            select(func.count(Document.id))
            .join(SourceFile, Document.source_file_id == SourceFile.id)
            .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
            .where(job_filter, Document.config_version_id == config_version_id)
        )
        or 0
    )
    chunk_count = (
        db.scalar(
            select(func.count(Chunk.id))
            .join(Document, Chunk.document_id == Document.id)
            .join(SourceFile, Document.source_file_id == SourceFile.id)
            .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
            .where(job_filter, Document.config_version_id == config_version_id)
        )
        or 0
    )
    text_vector_check: dict[str, Any] = {
        "required": config.routing.text_embedding_primary is not None,
        "expected": 0,
        "actual": 0,
        "dimension": None,
        "collection": None,
        "passed": True,
    }
    if config.routing.text_embedding_primary is not None:
        profile = next(
            item
            for item in config.models
            if item.profile_id == config.routing.text_embedding_primary
        )
        collection = text_collection_name(config, index_generation_id, profile.model_signature)
        text_vector_check.update({"expected": chunk_count, "collection": collection})
        try:
            stats = collection_stats(collection)
            text_vector_check.update(
                {
                    "actual": stats.points_count,
                    "dimension": stats.dimension,
                    "passed": (
                        stats.points_count == chunk_count
                        and stats.dimension == config.indexes.embedding_dimension
                    ),
                }
            )
        except Exception as exc:
            text_vector_check.update({"passed": False, "error": str(exc)[:500]})

    visual_rows = db.execute(
        select(Page.visual_model_signature, func.count(Page.id))
        .join(Document, Page.document_id == Document.id)
        .join(SourceFile, Document.source_file_id == SourceFile.id)
        .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
        .where(job_filter, Page.visual_status == "READY")
        .group_by(Page.visual_model_signature)
    ).all()
    visual_collections: list[dict[str, Any]] = []
    visual_vectors_passed = True
    for signature, expected in visual_rows:
        if not signature:
            visual_vectors_passed = False
            visual_collections.append(
                {"model_signature": None, "expected": expected, "actual": 0, "passed": False}
            )
            continue
        collection = visual_collection_name(config, index_generation_id, signature)
        entry: dict[str, Any] = {
            "model_signature": signature,
            "collection": collection,
            "expected": expected,
            "actual": 0,
            "passed": False,
        }
        try:
            stats = collection_stats(collection)
            entry.update(
                {
                    "actual": stats.points_count,
                    "dimension": stats.dimension,
                    "passed": stats.points_count == expected,
                }
            )
        except Exception as exc:
            entry["error"] = str(exc)[:500]
        visual_vectors_passed = visual_vectors_passed and bool(entry["passed"])
        visual_collections.append(entry)
    publish_rate = published_ready / supported if supported else 1.0
    checks = {
        "publish_rate": {
            "value": publish_rate,
            "required": config.publication.supported_publish_rate_min,
            "passed": publish_rate >= config.publication.supported_publish_rate_min,
        },
        "visual_ready": {"missing": missing_visual, "passed": missing_visual == 0},
        "embedding_ready": {
            "failed": failed_embeddings,
            "missing": incomplete_embeddings,
            "passed": failed_embeddings == 0 and incomplete_embeddings == 0,
        },
        "page_alignment": {
            "missing": missing_page_alignment,
            "passed": missing_page_alignment == 0,
        },
        "vector_counts": {
            "text": text_vector_check,
            "visual": {
                "expected": sum(int(expected) for _, expected in visual_rows),
                "actual": sum(int(item["actual"]) for item in visual_collections),
                "collections": visual_collections,
                "passed": visual_vectors_passed,
            },
            "passed": text_vector_check["passed"] and visual_vectors_passed,
        },
        "counts": {
            "supported_sources": supported,
            "published_ready_sources": published_ready,
            "documents": document_count,
            "chunks": chunk_count,
        },
    }
    required = [checks["publish_rate"]["passed"], checks["embedding_ready"]["passed"]]
    if config.publication.require_visual_ready_when_required:
        required.append(checks["visual_ready"]["passed"])
    if config.publication.require_no_missing_page_alignment:
        required.append(checks["page_alignment"]["passed"])
    required.append(checks["vector_counts"]["passed"])
    return {
        "config_version_id": config_version_id,
        "index_generation_id": index_generation_id,
        "passed": all(required),
        "checks": checks,
    }


def create_and_publish(
    db: Session,
    config_version_id: str,
    index_generation_id: str,
    *,
    activate: bool = True,
) -> Publication:
    validation = validate_publication(db, config_version_id, index_generation_id)
    existing = db.scalar(
        select(Publication).where(Publication.index_generation_id == index_generation_id)
    )
    if existing is not None:
        existing.validation = validation
        if not validation["passed"]:
            if not existing.active:
                existing.status = "REJECTED"
            db.commit()
            raise PublicationValidationError(validation)
        publication = existing
    else:
        publication = Publication(
            config_version_id=config_version_id,
            index_generation_id=index_generation_id,
            status="VALIDATED" if validation["passed"] else "REJECTED",
            active=False,
            validation=validation,
        )
        db.add(publication)
        db.flush()
        if not validation["passed"]:
            db.commit()
            raise PublicationValidationError(validation)
    if activate:
        config = RuntimeConfigBundleV1.model_validate(
            get_version(db, config_version_id).content
        )
        publishable_statuses = ["INDEXED"]
        if config.routing.text_embedding_primary is None:
            publishable_statuses.append("PARSED")
        db.execute(update(Publication).where(Publication.active.is_(True)).values(active=False))
        publication.active = True
        publication.status = "PUBLISHED"
        publication.published_at = datetime.now(UTC)
        db.execute(
            update(SourceFile)
            .where(
                SourceFile.job_id.in_(
                    select(IngestionJob.id).where(
                        IngestionJob.index_generation_id == index_generation_id
                    )
                ),
                SourceFile.status.in_(publishable_statuses),
            )
            .values(status="PUBLISHED")
        )
    db.commit()
    db.refresh(publication)
    if activate:
        # Keep job cards consistent with the source state changed by publishing.
        from docflow.services.jobs import refresh_job_counts

        jobs = list(
            db.scalars(
                select(IngestionJob).where(
                    IngestionJob.index_generation_id == index_generation_id
                )
            )
        )
        for job in jobs:
            refresh_job_counts(db, job)
    return publication
