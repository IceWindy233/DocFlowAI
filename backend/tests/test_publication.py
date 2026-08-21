from sqlalchemy.orm import Session

from docflow.db.models import Chunk, Document, Page, Publication, SourceFile
from docflow.domain.jobs import IngestionJobCreate
from docflow.services.config_service import ensure_default_config
from docflow.services.jobs import create_job
from docflow.services.publication import create_and_publish, validate_publication


def test_required_visual_failure_blocks_publication(db: Session, tmp_path) -> None:
    ensure_default_config(db)
    job = create_job(db, IngestionJobCreate(source_root=str(tmp_path)))
    source = SourceFile(
        job_id=job.id,
        source_path=str(tmp_path / "table.xlsx"),
        relative_path="table.xlsx",
        file_name="table.xlsx",
        extension=".xlsx",
        status="WAITING_REVIEW",
        sha256="0" * 64,
    )
    db.add(source)
    db.flush()
    document = Document(
        source_file_id=source.id,
        config_version_id=job.config_version_id,
        case_id="case_test",
        title="复杂表格",
        parser_route="NATIVE_XLSX",
        parser_version="openpyxl",
    )
    db.add(document)
    db.flush()
    db.add(
        Page(
            id=f"{document.id}_p0001",
            document_id=document.id,
            page_number=1,
            page_type="TABLE",
            visual_required=True,
            visual_status="FAILED",
        )
    )
    db.commit()
    result = validate_publication(db, job.config_version_id, job.index_generation_id)
    assert result["passed"] is False
    assert result["checks"]["visual_ready"] == {"missing": 1, "passed": False}


def test_visual_only_parsed_sources_are_publishable_for_legacy_jobs(
    db: Session, tmp_path, monkeypatch
) -> None:
    ensure_default_config(db)
    job = create_job(db, IngestionJobCreate(source_root=str(tmp_path)))
    source = SourceFile(
        job_id=job.id,
        source_path=str(tmp_path / "letter.doc"),
        relative_path="letter.doc",
        file_name="letter.doc",
        extension=".doc",
        status="PARSED",
        sha256="1" * 64,
    )
    db.add(source)
    db.flush()
    document = Document(
        source_file_id=source.id,
        config_version_id=job.config_version_id,
        case_id="case_test",
        title="关于测试事项的函",
        parser_route="TEST",
        parser_version="1",
    )
    db.add(document)
    db.flush()
    page = Page(
        id=f"{document.id}_p0001",
        document_id=document.id,
        page_number=1,
        page_type="SCAN",
        visual_required=True,
        visual_status="READY",
        visual_model_signature="vidore/colqwen2.5-v0.2",
    )
    db.add(page)
    db.flush()
    db.add(
        Chunk(
            id=f"{document.id}_c00001",
            document_id=document.id,
            page_id=page.id,
            ordinal=0,
            text="测试正文",
            embedding_status="DISABLED",
        )
    )
    db.commit()
    monkeypatch.setattr(
        "docflow.services.publication.collection_stats",
        lambda collection: type(
            "Stats", (), {"points_count": 1, "dimension": 128, "collection": collection}
        )(),
    )

    result = validate_publication(db, job.config_version_id, job.index_generation_id)
    assert result["passed"] is True
    assert result["checks"]["publish_rate"]["value"] == 1.0

    publication = create_and_publish(db, job.config_version_id, job.index_generation_id)
    assert publication.active is True
    assert db.get(SourceFile, source.id).status == "PUBLISHED"
    assert db.get(type(job), job.id).stage_counts == {"PUBLISHED": 1}

    repeated = create_and_publish(db, job.config_version_id, job.index_generation_id)
    assert repeated.id == publication.id
    assert db.query(Publication).count() == 1


def test_existing_publication_can_be_reactivated(db: Session, tmp_path, monkeypatch) -> None:
    ensure_default_config(db)
    monkeypatch.setattr(
        "docflow.services.publication.collection_stats",
        lambda collection: type(
            "Stats", (), {"points_count": 1, "dimension": 128, "collection": collection}
        )(),
    )

    def publish_job(name: str, digest: str) -> Publication:
        source_root = tmp_path / name
        source_root.mkdir()
        job = create_job(db, IngestionJobCreate(source_root=str(source_root)))
        source = SourceFile(
            job_id=job.id,
            source_path=str(tmp_path / name / "letter.pdf"),
            relative_path=f"{name}/letter.pdf",
            file_name="letter.pdf",
            extension=".pdf",
            status="PARSED",
            sha256=digest * 64,
        )
        db.add(source)
        db.flush()
        document = Document(
            source_file_id=source.id,
            config_version_id=job.config_version_id,
            case_id=f"case_{name}",
            title=name,
            parser_route="TEST",
            parser_version="1",
        )
        db.add(document)
        db.flush()
        page = Page(
            id=f"{document.id}_p0001",
            document_id=document.id,
            page_number=1,
            page_type="SCAN",
            visual_required=True,
            visual_status="READY",
            visual_model_signature="vidore/colqwen2.5-v0.2",
        )
        db.add(page)
        db.flush()
        db.add(
            Chunk(
                id=f"{document.id}_c00001",
                document_id=document.id,
                page_id=page.id,
                ordinal=0,
                text=name,
                embedding_status="DISABLED",
            )
        )
        db.commit()
        return create_and_publish(db, job.config_version_id, job.index_generation_id)

    first = publish_job("first", "1")
    second = publish_job("second", "2")
    assert first.active is False
    assert second.active is True

    reactivated = create_and_publish(
        db,
        first.config_version_id,
        first.index_generation_id,
    )
    assert reactivated.id == first.id
    assert reactivated.active is True
    assert db.get(Publication, second.id).active is False
    assert db.query(Publication).count() == 2
