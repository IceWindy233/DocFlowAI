from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from docflow.db.models import GoldenSample, SourceFile
from docflow.domain.golden import GoldenAnnotationUpdate, GoldenExpectedV1, GoldenReviewRequest
from docflow.domain.jobs import IngestionJobCreate
from docflow.services.config_service import ensure_default_config
from docflow.services.golden import (
    golden_report,
    replace_golden_sample,
    review_golden_sample,
    save_golden_annotation,
)
from docflow.services.jobs import create_job


def _sample(db: Session, tmp_path: Path) -> tuple[GoldenSample, SourceFile, SourceFile]:
    ensure_default_config(db)
    job = create_job(db, IngestionJobCreate(source_root=str(tmp_path)))
    first_path = tmp_path / "first.docx"
    second_path = tmp_path / "second.docx"
    first_path.write_bytes(b"invalid-docx-for-metadata-test")
    second_path.write_bytes(b"invalid-docx-for-metadata-test-2")
    first = SourceFile(
        job_id=job.id,
        source_path=str(first_path),
        relative_path="first.docx",
        file_name="first.docx",
        extension=".docx",
        status="READY",
        sha256="1" * 64,
    )
    second = SourceFile(
        job_id=job.id,
        source_path=str(second_path),
        relative_path="second.docx",
        file_name="second.docx",
        extension=".docx",
        status="READY",
        sha256="2" * 64,
    )
    db.add_all([first, second])
    db.flush()
    sample = GoldenSample(
        source_file_id=first.id,
        page_number=1,
        category="NATIVE_DOCX",
        selection_reason="test",
        annotation={"status": "PENDING", "expected": {}},
    )
    db.add(sample)
    db.commit()
    return sample, first, second


def test_golden_annotation_save_and_approve(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample, _, _ = _sample(db, tmp_path)
    monkeypatch.setattr(
        "docflow.services.golden.get_settings",
        lambda: SimpleNamespace(report_root=tmp_path),
    )
    saved = save_golden_annotation(
        db,
        sample.id,
        GoldenAnnotationUpdate(
            expected=GoldenExpectedV1(text="人工核对后的标准文本", title="测试标题"),
            notes="已核对",
            reviewer="tester",
        ),
    )
    assert saved["annotation"]["status"] == "ANNOTATED"
    approved = review_golden_sample(
        db,
        sample.id,
        "approve",
        GoldenReviewRequest(reviewer="reviewer"),
    )
    assert approved["annotation"]["status"] == "APPROVED"
    report = golden_report(db)
    assert report["approved"] == 1
    assert report["quality_ready"] is False
    assert (tmp_path / "golden-set.json").is_file()


def test_golden_approval_validates_and_candidate_can_be_replaced(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample, first, second = _sample(db, tmp_path)
    monkeypatch.setattr(
        "docflow.services.golden.get_settings",
        lambda: SimpleNamespace(report_root=tmp_path),
    )
    with pytest.raises(ValueError, match="标准文本"):
        review_golden_sample(
            db,
            sample.id,
            "approve",
            GoldenReviewRequest(reviewer="reviewer"),
        )
    replacement = replace_golden_sample(
        db,
        sample.id,
        GoldenReviewRequest(reviewer="reviewer", reason="原候选不符合类别"),
    )
    assert replacement["source_file_id"] == second.id
    assert replacement["source_file_id"] != first.id
    assert replacement["annotation"]["status"] == "PENDING"
    assert len(replacement["annotation"]["replacement_history"]) == 1
