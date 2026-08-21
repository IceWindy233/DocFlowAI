from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from docflow.db.models import Chunk, Document, ReviewTask, SourceFile
from docflow.domain.documents import PageV1
from docflow.domain.jobs import IngestionJobCreate
from docflow.services.config_service import ensure_default_config
from docflow.services.jobs import create_job
from docflow.services.parsers.base import ParserError
from docflow.services.pipeline import process_source


def test_parse_failure_creates_review_without_dangling_document_fk(
    tmp_path: Path, db: Session, monkeypatch
) -> None:
    source_path = tmp_path / "损坏文档.pdf"
    source_path.write_bytes(b"broken")
    ensure_default_config(db)
    job = create_job(db, IngestionJobCreate(source_root=str(tmp_path)))
    source = SourceFile(
        job_id=job.id,
        source_path=str(source_path),
        relative_path=source_path.name,
        file_name=source_path.name,
        extension=".pdf",
        status="READY",
        sha256="0" * 64,
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    monkeypatch.setattr(
        "docflow.services.pipeline.ParserRegistry.parse",
        lambda *_: (_ for _ in ()).throw(ParserError("测试解析失败")),
    )
    cleaned: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "docflow.services.pipeline.delete_visual_document_points",
        lambda _config, generation_id, document_id: cleaned.append(
            (generation_id, document_id)
        ),
    )

    process_source(db, job, source)

    review = db.scalar(select(ReviewTask).where(ReviewTask.source_file_id == source.id))
    assert review is not None
    assert review.document_id is None
    assert review.details["attempted_document_id"].startswith("doc_")
    assert db.get(SourceFile, source.id).status == "FAILED"
    assert cleaned == [(job.index_generation_id, review.details["attempted_document_id"])]


def test_multimodal_review_is_linked_after_document_persistence(
    tmp_path: Path, db: Session, monkeypatch
) -> None:
    source_path = tmp_path / "复杂页面.pdf"
    source_path.write_bytes(b"fake")
    ensure_default_config(db)
    job = create_job(db, IngestionJobCreate(source_root=str(tmp_path)))
    source = SourceFile(
        job_id=job.id,
        source_path=str(source_path),
        relative_path=source_path.name,
        file_name=source_path.name,
        extension=".pdf",
        status="READY",
        sha256="1" * 64,
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    def fake_parse(registry, context):
        from docflow.services.parsers.base import ParsedContent

        return ParsedContent(
            pages=[
                PageV1(
                    page_id=f"{context.document_id}_p0001",
                    page_number=1,
                    page_type="TABLE",
                    text="测试复杂表格",
                    parser_route="TEST",
                    quality_score=0.8,
                    visual_required=True,
                    visual_status="PENDING",
                )
            ],
            parser_route="TEST",
            parser_version="test",
        )

    monkeypatch.setattr("docflow.services.pipeline.ParserRegistry.parse", fake_parse)
    monkeypatch.setattr(
        "docflow.services.pipeline.write_document_artifacts", lambda _document: {}
    )
    monkeypatch.setattr(
        "docflow.services.pipeline.delete_visual_document_points", lambda *_: None
    )

    process_source(db, job, source)

    document = db.scalar(select(Document).where(Document.source_file_id == source.id))
    review = db.scalar(select(ReviewTask).where(ReviewTask.source_file_id == source.id))
    assert document is not None
    assert review is not None
    assert review.document_id == document.id
    assert db.get(SourceFile, source.id).status == "WAITING_REVIEW"


def test_visual_only_pipeline_marks_source_indexed_when_required_indexes_are_ready(
    tmp_path: Path, db: Session, monkeypatch
) -> None:
    source_path = tmp_path / "普通公文.doc"
    source_path.write_bytes(b"fake")
    ensure_default_config(db)
    job = create_job(db, IngestionJobCreate(source_root=str(tmp_path)))
    source = SourceFile(
        job_id=job.id,
        source_path=str(source_path),
        relative_path=source_path.name,
        file_name=source_path.name,
        extension=".doc",
        status="READY",
        sha256="2" * 64,
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    def fake_parse(registry, context):
        from docflow.services.parsers.base import ParsedContent

        return ParsedContent(
            pages=[
                PageV1(
                    page_id=f"{context.document_id}_p0001",
                    page_number=1,
                    text="关于测试事项的函\n测试正文。",
                    parser_route="TEST",
                    quality_score=1.0,
                )
            ],
            parser_route="TEST",
            parser_version="test",
        )

    monkeypatch.setattr("docflow.services.pipeline.ParserRegistry.parse", fake_parse)
    monkeypatch.setattr(
        "docflow.services.pipeline.write_document_artifacts", lambda _document: {}
    )
    monkeypatch.setattr(
        "docflow.services.pipeline.delete_visual_document_points", lambda *_: None
    )

    process_source(db, job, source)

    stored = db.get(SourceFile, source.id)
    chunks = list(db.scalars(select(Chunk)))
    assert stored.status == "INDEXED"
    assert chunks and {chunk.embedding_status for chunk in chunks} == {"DISABLED"}
