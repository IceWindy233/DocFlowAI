from __future__ import annotations

import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from docflow.db.models import SourceFile
from docflow.domain.jobs import IngestionJobCreate, IngestionOptions
from docflow.services.config_service import ensure_default_config
from docflow.services.inventory import inventory_job
from docflow.services.jobs import create_job


def test_inventory_tracks_every_file_and_duplicate(tmp_path: Path, db: Session) -> None:
    corpus = tmp_path / "请示材料" / "2026年" / "案例一"
    corpus.mkdir(parents=True)
    (corpus / "正文.pdf").write_bytes(b"%PDF-not-a-real-pdf")
    (corpus / "正文副本.pdf").write_bytes(b"%PDF-not-a-real-pdf")
    (corpus / "~$正文.docx").write_bytes(b"temporary")
    (corpus / "shortcut.lnk").write_bytes(b"shortcut")
    with zipfile.ZipFile(corpus / "附件.zip", "w") as archive:
        archive.writestr("safe/readme.txt", "ok")
        archive.writestr("../escape.txt", "unsafe")

    ensure_default_config(db)
    job = create_job(
        db,
        IngestionJobCreate(
            source_root=str(tmp_path),
            options=IngestionOptions(inventory_only=True),
        ),
    )
    report = inventory_job(db, job)
    assert report["summary"]["total_files"] == 5
    assert report["summary"]["status_counts"] == {
        "DUPLICATE": 1,
        "READY": 1,
        "SKIPPED_TEMP": 1,
        "UNSUPPORTED": 2,
    }
    sources = list(db.scalars(select(SourceFile).where(SourceFile.job_id == job.id)))
    archive = next(item for item in sources if item.extension == ".zip")
    assert len(archive.archive_entries) == 2
    assert any(entry["unsafe"] for entry in archive.archive_entries)
    duplicate = next(item for item in sources if item.status == "DUPLICATE")
    assert duplicate.canonical_source_id is not None


def test_inventory_is_idempotent(tmp_path: Path, db: Session) -> None:
    corpus = tmp_path / "函件材料"
    corpus.mkdir()
    (corpus / "材料.docx").write_bytes(b"not-a-real-docx")
    ensure_default_config(db)
    job = create_job(db, IngestionJobCreate(source_root=str(tmp_path)))
    first = inventory_job(db, job)
    second = inventory_job(db, job)
    assert first["summary"]["total_files"] == second["summary"]["total_files"] == 1


def test_inventory_supports_multiple_roots_and_cross_root_duplicates(
    tmp_path: Path, db: Session
) -> None:
    first_root = tmp_path / "第一批"
    second_root = tmp_path / "第二批"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "正文.docx").write_bytes(b"same-content")
    (second_root / "正文副本.docx").write_bytes(b"same-content")
    (second_root / "附件.pdf").write_bytes(b"%PDF-unique")

    ensure_default_config(db)
    job = create_job(
        db,
        IngestionJobCreate(
            source_roots=[str(first_root), str(second_root)],
            options=IngestionOptions(inventory_only=True),
        ),
    )
    report = inventory_job(db, job)

    assert report["source_roots"] == [str(first_root), str(second_root)]
    assert report["summary"]["total_files"] == 3
    assert report["summary"]["status_counts"] == {"DUPLICATE": 1, "READY": 2}
    sources = list(db.scalars(select(SourceFile).where(SourceFile.job_id == job.id)))
    assert {Path(item.relative_path).parts[0] for item in sources} == {"第一批", "第二批"}


def test_nested_source_roots_are_collapsed(tmp_path: Path, db: Session) -> None:
    parent = tmp_path / "语料"
    child = parent / "案件"
    child.mkdir(parents=True)
    (child / "正文.docx").write_bytes(b"content")
    ensure_default_config(db)

    job = create_job(
        db,
        IngestionJobCreate(source_roots=[str(child), str(parent), str(child)]),
    )

    assert job.source_root == str(parent)
    assert job.options["source_roots"] == [str(parent)]
