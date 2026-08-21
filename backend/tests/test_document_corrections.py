from sqlalchemy import select
from sqlalchemy.orm import Session

from docflow.api.routers.documents import correct_document
from docflow.db.models import AuditEvent, Document, SourceFile
from docflow.domain.jobs import DocumentCorrectionRequest, IngestionJobCreate
from docflow.services.config_service import ensure_default_config
from docflow.services.jobs import create_job


def _document(db: Session, job, suffix: str, *, selected: bool) -> Document:
    source = SourceFile(
        job_id=job.id,
        source_path=f"/tmp/{suffix}.pdf",
        relative_path=f"{suffix}.pdf",
        file_name=f"{suffix}.pdf",
        extension=".pdf",
        status="INDEXED",
        sha256=suffix * 64,
    )
    db.add(source)
    db.flush()
    document = Document(
        source_file_id=source.id,
        config_version_id=job.config_version_id,
        case_id="case_same",
        title=f"旧标题{suffix}",
        document_number="旧文号",
        document_role="LETTER",
        version_role="DRAFT",
        authority_score=0.3,
        selected=selected,
        parser_route="TEST",
        parser_version="1",
        normalized={"title": f"旧标题{suffix}", "document_number": "旧文号"},
    )
    db.add(document)
    db.flush()
    return document


def test_document_correction_syncs_normalized_and_switches_authority(
    db: Session, tmp_path, monkeypatch
) -> None:
    ensure_default_config(db)
    job = create_job(db, IngestionJobCreate(source_root=str(tmp_path)))
    old = _document(db, job, "a", selected=True)
    replacement = _document(db, job, "b", selected=False)
    db.commit()
    written: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "docflow.api.routers.documents.LocalArtifactStore.write_json",
        lambda _store, path, value: written.append((path, value)) or path,
    )

    result = correct_document(
        replacement.id,
        DocumentCorrectionRequest(
            title="关于测试事项的函",
            document_number="示例函〔2027〕1号",
            document_role="LETTER",
            version_role="FORMAL",
            authority_score=0.95,
            selected=True,
            reason="人工核对元数据",
        ),
        db,
    )

    db.refresh(old)
    db.refresh(replacement)
    assert old.selected is False
    assert result["selected"] is True
    assert replacement.normalized["title"] == "关于测试事项的函"
    assert replacement.normalized["document_number"] == "示例函〔2027〕1号"
    assert written[0][0] == f"{replacement.id}/normalized-document.json"
    assert db.scalar(select(AuditEvent).where(AuditEvent.target_id == replacement.id))
