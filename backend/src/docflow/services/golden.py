from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from docflow.core.settings import get_settings
from docflow.db.models import AuditEvent, GoldenSample, IngestionJob, SourceFile
from docflow.domain.golden import (
    GoldenAnnotationUpdate,
    GoldenAnnotationV1,
    GoldenReviewRequest,
)
from docflow.services.golden_preview import suggested_text

GOLDEN_TARGETS = {
    "NATIVE_DOCX": 20,
    "LEGACY_DOC_WPS": 15,
    "SCANNED_DOCUMENT": 20,
    "STAMPED_REPLY": 10,
    "MEETING_FORM": 15,
    "COMPLEX_TABLE": 15,
    "MIXED_CONTENT": 5,
}


@lru_cache(maxsize=2048)
def _pdf_sparse_pages(path_text: str) -> frozenset[int]:
    try:
        reader = PdfReader(path_text, strict=False)
        return frozenset(
            page_number
            for page_number, page in enumerate(reader.pages[:10], start=1)
            if len((page.extract_text() or "").strip()) < 20
        )
    except Exception:
        return frozenset()


def _pdf_sparse_text(path: Path, page_number: int) -> bool:
    return page_number in _pdf_sparse_pages(str(path))


def _category_score(source: SourceFile, page_number: int, category: str) -> int:
    text = f"{source.relative_path} {source.file_name}".lower()
    ext = source.extension
    score = 0
    if category == "NATIVE_DOCX":
        score += 100 if ext == ".docx" else 0
    elif category == "LEGACY_DOC_WPS":
        score += 100 if ext in {".doc", ".wps"} else 0
    elif category == "SCANNED_DOCUMENT":
        score += (
            70 if ext == ".pdf" and _pdf_sparse_text(Path(source.source_path), page_number) else 0
        )
        score += 30 if ext in {".jpg", ".jpeg", ".png", ".tif", ".tiff"} else 0
    elif category == "STAMPED_REPLY":
        score += 60 if re.search(r"批复|复函|答复|意见|回复|回函", text) else 0
        score += 20 if re.search(r"函〔?\d{4}〕?\d+号", text) else 0
    elif category == "MEETING_FORM":
        score += 70 if re.search(r"会议|纪要|会审|审查|签到|议程", text) else 0
    elif category == "COMPLEX_TABLE":
        score += 70 if ext in {".xls", ".xlsx"} else 0
        score += 50 if re.search(r"表|清单|统计|汇总|明细", text) else 0
    elif category == "MIXED_CONTENT":
        score += 50 if ext in {".docx", ".pdf"} and re.search(r"图|附件|方案|示意", text) else 0
    return score


def select_golden_set(db: Session, job: IngestionJob, *, replace: bool = False) -> dict[str, Any]:
    if replace:
        db.execute(delete(GoldenSample))
        db.commit()
    elif db.scalar(select(GoldenSample.id).limit(1)):
        return golden_report(db)

    sources = list(
        db.scalars(
            select(SourceFile)
            .where(SourceFile.job_id == job.id, SourceFile.status == "READY")
            .order_by(SourceFile.sha256, SourceFile.relative_path)
        )
    )
    candidates: list[tuple[SourceFile, int]] = []
    for source in sources:
        page_count = min(source.page_count or 1, 10)
        candidates.extend((source, page) for page in range(1, page_count + 1))

    used: set[tuple[str, int]] = set()
    selected_counts: dict[str, int] = defaultdict(int)
    for category, target in GOLDEN_TARGETS.items():
        ranked = sorted(
            candidates,
            key=lambda item: (
                -_category_score(item[0], item[1], category),
                item[0].sha256 or item[0].id,
                item[1],
            ),
        )
        for source, page_number in ranked:
            key = (source.id, page_number)
            if key in used:
                continue
            score = _category_score(source, page_number, category)
            if score == 0 and selected_counts[category] < target:
                # Deterministic fallback keeps the benchmark complete even when metadata is sparse.
                score = 1
            db.add(
                GoldenSample(
                    source_file_id=source.id,
                    page_number=page_number,
                    category=category,
                    selection_reason=f"启发式评分 {score}；待人工标注确认",
                    annotation={"status": "PENDING", "expected": {}},
                )
            )
            used.add(key)
            selected_counts[category] += 1
            if selected_counts[category] >= target:
                break
    db.commit()
    return golden_report(db)


def _normalized_annotation(value: dict[str, Any] | None) -> GoldenAnnotationV1:
    return GoldenAnnotationV1.model_validate(value or {})


def _has_expected_content(annotation: GoldenAnnotationV1) -> bool:
    expected = annotation.expected
    return bool(
        expected.text.strip()
        or expected.title
        or expected.document_number
        or expected.numeric_fields
        or expected.table_data
        or expected.layout_elements
        or expected.visual_queries
    )


def annotation_validation_errors(category: str, annotation: GoldenAnnotationV1) -> list[str]:
    errors: list[str] = []
    if not annotation.expected.text.strip():
        errors.append("标准文本不能为空")
    if category == "COMPLEX_TABLE" and not annotation.expected.table_data:
        errors.append("复杂表格必须填写表格结构 JSON")
    if category == "MIXED_CONTENT" and not annotation.expected.visual_queries:
        errors.append("混合图文页面至少需要一条视觉检索问题")
    return errors


def _annotation_hash(value: dict[str, Any]) -> str:
    import hashlib

    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _audit_annotation(
    db: Session,
    sample: GoldenSample,
    event_type: str,
    before: dict[str, Any],
    after: dict[str, Any],
    actor: str,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            event_type=event_type,
            actor=actor,
            target_type="golden_sample",
            target_id=sample.id,
            before_hash=_annotation_hash(before),
            after_hash=_annotation_hash(after),
            details=details or {},
        )
    )


def _sample_item(sample: GoldenSample, source: SourceFile) -> dict[str, Any]:
    annotation = _normalized_annotation(sample.annotation)
    return {
        "id": sample.id,
        "source_file_id": sample.source_file_id,
        "page_number": sample.page_number,
        "category": sample.category,
        "selection_reason": sample.selection_reason,
        "annotation": annotation.model_dump(mode="json"),
        "validation_errors": annotation_validation_errors(sample.category, annotation),
        "source": {
            "relative_path": source.relative_path,
            "file_name": source.file_name,
            "extension": source.extension,
            "mime_type": source.mime_type,
            "sha256": source.sha256,
            "page_count": source.page_count,
        },
        "created_at": sample.created_at.isoformat(),
    }


def get_golden_sample(db: Session, sample_id: str) -> tuple[GoldenSample, SourceFile]:
    row = db.execute(
        select(GoldenSample, SourceFile)
        .join(SourceFile, SourceFile.id == GoldenSample.source_file_id)
        .where(GoldenSample.id == sample_id)
    ).one_or_none()
    if row is None:
        raise LookupError("Golden Sample 不存在")
    return row[0], row[1]


def golden_detail(db: Session, sample_id: str) -> dict[str, Any]:
    sample, source = get_golden_sample(db, sample_id)
    result = _sample_item(sample, source)
    result["suggested_text"] = suggested_text(source, sample.page_number)[:100_000]
    return result


def golden_report(db: Session) -> dict[str, Any]:
    rows = db.execute(
        select(GoldenSample, SourceFile)
        .join(SourceFile, SourceFile.id == GoldenSample.source_file_id)
        .order_by(GoldenSample.category, GoldenSample.id)
    ).all()
    counts: dict[str, int] = defaultdict(int)
    status_counts: dict[str, int] = defaultdict(int)
    items: list[dict[str, Any]] = []
    for sample, source in rows:
        counts[sample.category] += 1
        item = _sample_item(sample, source)
        status_counts[item["annotation"]["status"]] += 1
        items.append(item)
    total = len(items)
    approved = status_counts["APPROVED"]
    categories_ready = all(counts.get(name, 0) == target for name, target in GOLDEN_TARGETS.items())
    if total and approved == total:
        annotation_status = "COMPLETE"
    elif any(status != "PENDING" and count for status, count in status_counts.items()):
        annotation_status = "IN_PROGRESS"
    else:
        annotation_status = "PENDING"
    report = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "target_counts": GOLDEN_TARGETS,
        "actual_counts": dict(counts),
        "status_counts": dict(status_counts),
        "total": total,
        "approved": approved,
        "completion_percent": round(approved / total * 100, 2) if total else 0,
        "annotation_status": annotation_status,
        "quality_ready": bool(
            total
            and approved == total
            and categories_ready
            and not status_counts["REJECTED"]
        ),
        "samples": items,
    }
    output = get_settings().report_root / "golden-set.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def save_golden_annotation(
    db: Session,
    sample_id: str,
    payload: GoldenAnnotationUpdate,
) -> dict[str, Any]:
    sample, _ = get_golden_sample(db, sample_id)
    before = dict(sample.annotation or {})
    existing = _normalized_annotation(before)
    annotation = GoldenAnnotationV1(
        status="ANNOTATED" if _has_expected_content(
            GoldenAnnotationV1(expected=payload.expected)
        ) else "PENDING",
        expected=payload.expected,
        notes=payload.notes,
        reviewer=payload.reviewer,
        updated_at=datetime.now(UTC).isoformat(),
        replacement_history=existing.replacement_history,
    )
    after = annotation.model_dump(mode="json")
    sample.annotation = after
    _audit_annotation(db, sample, "GOLDEN_ANNOTATION_SAVED", before, after, payload.reviewer)
    db.commit()
    golden_report(db)
    return golden_detail(db, sample_id)


def review_golden_sample(
    db: Session,
    sample_id: str,
    action: str,
    payload: GoldenReviewRequest,
) -> dict[str, Any]:
    sample, _ = get_golden_sample(db, sample_id)
    before = dict(sample.annotation or {})
    annotation = _normalized_annotation(before)
    now = datetime.now(UTC).isoformat()
    if action == "approve":
        errors = annotation_validation_errors(sample.category, annotation)
        if errors:
            raise ValueError("；".join(errors))
        annotation.status = "APPROVED"
        annotation.approved_at = now
        annotation.rejection_reason = None
    elif action == "reject":
        if len(payload.reason.strip()) < 2:
            raise ValueError("拒绝候选时必须填写原因")
        annotation.status = "REJECTED"
        annotation.approved_at = None
        annotation.rejection_reason = payload.reason.strip()
    else:
        raise ValueError("不支持的审核操作")
    annotation.reviewer = payload.reviewer
    annotation.updated_at = now
    after = annotation.model_dump(mode="json")
    sample.annotation = after
    _audit_annotation(
        db,
        sample,
        f"GOLDEN_{action.upper()}",
        before,
        after,
        payload.reviewer,
        {"reason": payload.reason},
    )
    db.commit()
    golden_report(db)
    return golden_detail(db, sample_id)


def replace_golden_sample(
    db: Session,
    sample_id: str,
    payload: GoldenReviewRequest,
) -> dict[str, Any]:
    if len(payload.reason.strip()) < 2:
        raise ValueError("替换候选时必须填写原因")
    sample, source = get_golden_sample(db, sample_id)
    used = {
        (source_file_id, page_number)
        for source_file_id, page_number in db.execute(
            select(GoldenSample.source_file_id, GoldenSample.page_number)
        )
    }
    sources = list(
        db.scalars(
            select(SourceFile)
            .where(SourceFile.job_id == source.job_id, SourceFile.status == "READY")
            .order_by(SourceFile.sha256, SourceFile.relative_path)
        )
    )
    candidates: list[tuple[int, SourceFile, int]] = []
    for candidate in sources:
        for page_number in range(1, min(candidate.page_count or 1, 10) + 1):
            if (candidate.id, page_number) not in used:
                candidates.append(
                    (
                        _category_score(candidate, page_number, sample.category),
                        candidate,
                        page_number,
                    )
                )
    if not candidates:
        raise ValueError("没有可用的替换候选")
    score, replacement, page_number = sorted(
        candidates,
        key=lambda item: (-item[0], item[1].sha256 or item[1].id, item[2]),
    )[0]
    before = dict(sample.annotation or {})
    existing = _normalized_annotation(before)
    history = [
        *existing.replacement_history,
        {
            "source_file_id": source.id,
            "relative_path": source.relative_path,
            "page_number": sample.page_number,
            "reason": payload.reason.strip(),
            "reviewer": payload.reviewer,
            "replaced_at": datetime.now(UTC).isoformat(),
        },
    ]
    sample.source_file_id = replacement.id
    sample.page_number = page_number
    sample.selection_reason = f"人工替换候选；启发式评分 {score}"
    annotation = GoldenAnnotationV1(
        reviewer=payload.reviewer,
        updated_at=datetime.now(UTC).isoformat(),
        replacement_history=history,
    )
    after = annotation.model_dump(mode="json")
    sample.annotation = after
    _audit_annotation(
        db,
        sample,
        "GOLDEN_REPLACED",
        before,
        after,
        payload.reviewer,
        {"previous_source_file_id": source.id, "reason": payload.reason.strip()},
    )
    db.commit()
    golden_report(db)
    return golden_detail(db, sample_id)
