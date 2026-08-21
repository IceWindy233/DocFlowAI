from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from docflow.core.settings import get_settings
from docflow.db.models import Document, IngestionJob, Page, ReviewTask, SourceFile


def benchmark_report(db: Session, job: IngestionJob) -> dict[str, Any]:
    documents = list(
        db.scalars(
            select(Document)
            .join(SourceFile, Document.source_file_id == SourceFile.id)
            .where(SourceFile.job_id == job.id)
        )
    )
    pages = list(
        db.scalars(
            select(Page)
            .join(Document, Page.document_id == Document.id)
            .join(SourceFile, Document.source_file_id == SourceFile.id)
            .where(SourceFile.job_id == job.id)
        )
    )
    reviews = list(db.scalars(select(ReviewTask).where(ReviewTask.job_id == job.id)))
    supported_total = (
        db.scalar(
            select(func.count(SourceFile.id)).where(
                SourceFile.status.notin_(["SKIPPED_TEMP", "UNSUPPORTED", "DUPLICATE"])
            )
        )
        or 0
    )
    processed_supported = max(1, len(documents))
    usage = job.cloud_usage or {}
    scale = supported_total / processed_supported
    report = {
        "schema_version": "1.0",
        "job_id": job.id,
        "config_version_id": job.config_version_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "sample": {
            "documents": len(documents),
            "pages": len(pages),
            "average_quality": (
                round(sum(page.quality_score for page in pages) / len(pages), 4) if pages else None
            ),
            "visual_status": dict(Counter(page.visual_status for page in pages)),
            "review_categories": dict(Counter(item.category for item in reviews)),
        },
        "cloud_usage": usage,
        "full_run_estimate": {
            "supported_files": supported_total,
            "scale_factor": round(scale, 4),
            "estimated_calls": round(float(usage.get("calls", 0)) * scale),
            "estimated_cost_cny": round(float(usage.get("estimated_cost_cny", 0)) * scale, 4),
            "requires_user_confirmation": True,
        },
        "quality_metrics_status": "PENDING_GOLDEN_ANNOTATION",
    }
    output = get_settings().report_root / f"benchmark-{job.id}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
