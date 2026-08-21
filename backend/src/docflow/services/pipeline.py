from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, aliased

from docflow.db.models import (
    Chunk,
    Document,
    IngestionJob,
    JobStatus,
    Page,
    ReviewTask,
    SourceFile,
    SourceStatus,
    new_id,
)
from docflow.domain.config import RuntimeConfigBundleV1
from docflow.domain.documents import ChunkV1, NormalizedDocumentV1, PageV1
from docflow.services.config_service import get_version
from docflow.services.inventory import inventory_job, write_inventory_report
from docflow.services.jobs import refresh_job_counts, transition_job
from docflow.services.model_gateway import (
    CloudBudgetExceeded,
    CloudModelError,
    embed_texts,
    enhance_page_with_vlm,
)
from docflow.services.parsers import ParserRegistry
from docflow.services.parsers.base import ParseContext, ParserError
from docflow.services.parsers.common import (
    infer_document_number,
    infer_document_role,
    infer_title,
    infer_version_role,
)
from docflow.services.storage import LocalArtifactStore
from docflow.services.vector_index import (
    VisualIndexError,
    delete_visual_document_points,
    index_text_vectors,
    index_visual_page,
)


def stable_case_id(source: SourceFile) -> str:
    hint = source.case_hint or str(Path(source.relative_path).parent)
    digest = hashlib.sha256(hint.encode("utf-8")).hexdigest()[:16]
    return f"case_{digest}"


def make_chunks(document: NormalizedDocumentV1, config: RuntimeConfigBundleV1) -> list[ChunkV1]:
    chunks: list[ChunkV1] = []
    ordinal = 0
    max_chars = config.chunking.target_max_chars
    min_chars = config.chunking.target_min_chars
    for page in document.pages:
        paragraphs = [part.strip() for part in page.text.splitlines() if part.strip()]
        buffer = ""
        for paragraph in paragraphs:
            candidate = f"{buffer}\n{paragraph}".strip()
            if buffer and len(candidate) > max_chars:
                chunks.append(
                    ChunkV1(
                        chunk_id=f"{document.document_id}_c{ordinal:05d}",
                        document_id=document.document_id,
                        page_id=page.page_id,
                        ordinal=ordinal,
                        text=buffer,
                        metadata={"page_number": page.page_number, "source": "paragraph"},
                    )
                )
                ordinal += 1
                buffer = paragraph
            else:
                buffer = candidate
            if len(buffer) >= min_chars and paragraph.endswith(("。", "！", "？", ";", "；")):
                chunks.append(
                    ChunkV1(
                        chunk_id=f"{document.document_id}_c{ordinal:05d}",
                        document_id=document.document_id,
                        page_id=page.page_id,
                        ordinal=ordinal,
                        text=buffer,
                        metadata={"page_number": page.page_number, "source": "paragraph"},
                    )
                )
                ordinal += 1
                buffer = ""
        if buffer:
            chunks.append(
                ChunkV1(
                    chunk_id=f"{document.document_id}_c{ordinal:05d}",
                    document_id=document.document_id,
                    page_id=page.page_id,
                    ordinal=ordinal,
                    text=buffer,
                    metadata={"page_number": page.page_number, "source": "paragraph"},
                )
            )
            ordinal += 1
        for table in page.tables:
            table_text = table.serialized_text or table.html
            if table_text:
                chunks.append(
                    ChunkV1(
                        chunk_id=f"{document.document_id}_c{ordinal:05d}",
                        document_id=document.document_id,
                        page_id=page.page_id,
                        ordinal=ordinal,
                        kind="table",
                        text=table_text,
                        metadata={
                            "page_number": page.page_number,
                            "source": "table",
                            "table_id": table.table_id,
                            "complex": table.complex,
                        },
                    )
                )
                ordinal += 1
    return chunks


def write_document_artifacts(document: NormalizedDocumentV1) -> dict[str, Any]:
    store = LocalArtifactStore()
    base = document.document_id
    docling_markdown = document.metadata.get("docling_markdown")
    markdown = (
        str(docling_markdown)
        if docling_markdown
        else "\n\n".join(
            f"<!-- page_id: {page.page_id}; page: {page.page_number} -->\n\n"
            f"{page.markdown or page.text}"
            for page in document.pages
        )
    )
    manifest: dict[str, Any] = {
        "markdown": store.write_bytes(f"{base}/document.md", markdown.encode("utf-8")),
        "normalized_json": str(store.root / base / "normalized-document.json"),
        "tables": [],
    }
    for page in document.pages:
        for table in page.tables:
            table_base = f"{base}/tables/{table.table_id}"
            manifest["tables"].append(
                {
                    "table_id": table.table_id,
                    "page_id": page.page_id,
                    "json": store.write_json(
                        f"{table_base}.json", table.model_dump(mode="json")
                    ),
                    "html": store.write_bytes(
                        f"{table_base}.html", table.html.encode("utf-8")
                    ),
                    "text": store.write_bytes(
                        f"{table_base}.txt", table.serialized_text.encode("utf-8")
                    ),
                    "screenshot": table.screenshot_path,
                }
            )
    store.write_json(f"{base}/artifact-manifest.json", manifest)
    return manifest


def _review(
    db: Session,
    *,
    job: IngestionJob,
    source: SourceFile,
    document_id: str | None,
    category: str,
    summary: str,
    details: dict[str, Any],
    severity: str = "WARNING",
) -> None:
    db.add(
        ReviewTask(
            job_id=job.id,
            source_file_id=source.id,
            document_id=document_id,
            category=category,
            severity=severity,
            summary=summary,
            details=details,
        )
    )


def _should_vlm(page: PageV1, config: RuntimeConfigBundleV1) -> bool:
    return (
        any(table.complex for table in page.tables)
        or page.page_type in {"MIXED", "STAMPED"}
        or page.quality_score < config.quality.warning_score
    )


def _apply_multimodal(
    db: Session,
    job: IngestionJob,
    source: SourceFile,
    document_id: str,
    pages: list[PageV1],
    config: RuntimeConfigBundleV1,
) -> bool:
    all_required_ready = True
    vlm_profile = next(
        (
            profile
            for profile in config.models
            if profile.profile_id == config.routing.vlm_primary and profile.enabled
        ),
        None,
    )
    vlm_enabled_for_job = bool(
        vlm_profile
        and config.budget.cloud_processing_allowed
        and job.options.get("cloud_processing_allowed")
    )
    for page in pages:
        if _should_vlm(page, config) and vlm_enabled_for_job:
            try:
                result = enhance_page_with_vlm(db, job, config, page)
                if result.markdown:
                    page.markdown = result.markdown
                page.quality_signals["vlm_confidence"] = result.confidence
                page.quality_signals["vlm_summary"] = result.summary
                page.quality_signals["vlm_enhanced"] = True
            except (CloudBudgetExceeded, CloudModelError) as exc:
                page.quality_signals["vlm_enhanced"] = False
                _review(
                    db,
                    job=job,
                    source=source,
                    document_id=None,
                    category="VLM_REQUIRED",
                    summary=f"第 {page.page_number} 页需要复杂页面增强",
                    details={
                        "page_id": page.page_id,
                        "reason": str(exc),
                        "attempted_document_id": document_id,
                    },
                )
        elif _should_vlm(page, config):
            page.quality_signals["vlm_enhanced"] = False
            page.quality_signals["vlm_route"] = "SKIPPED_OPTIONAL"

        if not page.visual_required:
            continue
        if not page.image_path or not Path(page.image_path).exists():
            page.visual_status = "FAILED"
            all_required_ready = False
            _review(
                db,
                job=job,
                source=source,
                document_id=None,
                category="VISUAL_IMAGE_MISSING",
                summary=f"第 {page.page_number} 页缺少视觉索引截图",
                details={"page_id": page.page_id, "attempted_document_id": document_id},
                severity="ERROR",
            )
            continue
        try:
            result = index_visual_page(
                config,
                job.index_generation_id,
                page.page_id,
                document_id,
                Path(page.image_path),
            )
            page.visual_status = "READY"
            page.quality_signals["visual_collection"] = result.collection
            page.quality_signals["visual_vector_count"] = result.vector_count
            page.quality_signals["visual_dimension"] = result.dimension
            page.quality_signals["visual_model_signature"] = result.model_signature
        except VisualIndexError as exc:
            page.visual_status = "FAILED"
            all_required_ready = False
            _review(
                db,
                job=job,
                source=source,
                document_id=None,
                category="VISUAL_INDEX_FAILED",
                summary=f"第 {page.page_number} 页视觉索引失败",
                details={
                    "page_id": page.page_id,
                    "reason": str(exc),
                    "attempted_document_id": document_id,
                },
                severity="ERROR",
            )
    return all_required_ready


def _store_document(
    db: Session,
    source: SourceFile,
    job: IngestionJob,
    normalized: NormalizedDocumentV1,
    authority_score: float,
) -> tuple[Document, list[Chunk]]:
    quality = (
        sum(page.quality_score for page in normalized.pages) / len(normalized.pages)
        if normalized.pages
        else 0.0
    )
    document = Document(
        id=normalized.document_id,
        source_file_id=source.id,
        config_version_id=job.config_version_id,
        case_id=normalized.case_id,
        title=normalized.title,
        document_number=normalized.document_number,
        document_role=normalized.document_role,
        version_role=normalized.version_role,
        authority_score=authority_score,
        selected=False,
        parser_route=normalized.parser_route,
        parser_version=normalized.parser_version,
        quality_score=quality,
        normalized=normalized.model_dump(mode="json"),
    )
    db.add(document)
    # SQLAlchemy cannot infer ORM dependency ordering from scalar FK values alone.
    # Persist the parent before adding Page/Chunk rows so PostgreSQL FK checks are stable.
    db.flush([document])
    page_records: list[Page] = []
    for page in normalized.pages:
        page_records.append(
            Page(
                id=page.page_id,
                document_id=document.id,
                page_number=page.page_number,
                page_type=page.page_type,
                text=page.text,
                content=page.model_dump(mode="json"),
                quality_score=page.quality_score,
                image_path=page.image_path,
                visual_required=page.visual_required,
                visual_status=page.visual_status,
                visual_model_signature=page.quality_signals.get("visual_model_signature"),
            )
        )
    db.add_all(page_records)
    db.flush(page_records)
    chunks_v1 = make_chunks(
        normalized,
        RuntimeConfigBundleV1.model_validate(job.config_version.content),
    )
    chunks: list[Chunk] = []
    for chunk in chunks_v1:
        record = Chunk(
            id=chunk.chunk_id,
            document_id=chunk.document_id,
            page_id=chunk.page_id,
            ordinal=chunk.ordinal,
            kind=chunk.kind,
            text=chunk.text,
            metadata_json=chunk.metadata,
            embedding_status="PENDING",
        )
        db.add(record)
        chunks.append(record)
    db.flush(chunks)
    return document, chunks


def _index_chunks(
    db: Session,
    job: IngestionJob,
    config: RuntimeConfigBundleV1,
    chunks: list[Chunk],
) -> bool:
    if not config.routing.text_embedding_primary:
        for chunk in chunks:
            chunk.embedding_status = "DISABLED"
        return False
    documents: dict[str, Document] = {}

    def embedding_text(chunk: Chunk) -> str:
        document = documents.get(chunk.document_id)
        if document is None:
            document = db.get(Document, chunk.document_id)
            if document is not None:
                documents[chunk.document_id] = document
        if document is None:
            return chunk.text
        page_number = chunk.metadata_json.get("page_number", "")
        return "\n".join(
            part
            for part in [
                f"标题：{document.title}" if document.title else "",
                f"文号：{document.document_number}" if document.document_number else "",
                f"文档角色：{document.document_role}",
                f"案件：{document.case_id}",
                f"页码：{page_number}" if page_number else "",
                f"内容类型：{chunk.kind}",
                f"正文：{chunk.text}",
            ]
            if part
        )
    try:
        for start in range(0, len(chunks), 16):
            batch = chunks[start : start + 16]
            vectors, signature = embed_texts(
                db,
                job,
                config,
                [embedding_text(chunk) for chunk in batch],
            )
            index_text_vectors(
                config,
                job.index_generation_id,
                signature,
                [
                    (
                        chunk.id,
                        vector,
                        {
                            "chunk_id": chunk.id,
                            "document_id": chunk.document_id,
                            "page_id": chunk.page_id,
                            **chunk.metadata_json,
                        },
                    )
                    for chunk, vector in zip(batch, vectors, strict=True)
                ],
            )
            for chunk in batch:
                chunk.embedding_status = "READY"
                chunk.embedding_signature = signature
        return True
    except (CloudBudgetExceeded, CloudModelError, Exception) as exc:
        # Qdrant/client errors and provider errors are both explicit index failures.
        for chunk in chunks:
            if chunk.embedding_status == "PENDING":
                chunk.embedding_status = "FAILED"
        raise RuntimeError(f"文本索引失败：{exc}") from exc


def process_source(db: Session, job: IngestionJob, source: SourceFile) -> None:
    source.status = SourceStatus.PROCESSING.value
    db.add(source)
    db.commit()
    config_version = get_version(db, job.config_version_id)
    config = RuntimeConfigBundleV1.model_validate(config_version.content)
    job.config_version = config_version
    document_id = new_id("doc")
    try:
        if source.sha256 is None:
            raise ParserError("源文件缺少 SHA-256")
        existing = db.scalar(select(Document).where(Document.source_file_id == source.id))
        if existing and not job.options.get("force_reparse"):
            source.status = SourceStatus.INDEXED.value
            db.commit()
            return
        if existing:
            db.execute(delete(Document).where(Document.id == existing.id))
            db.commit()

        context = ParseContext(
            document_id=document_id,
            source_file_id=source.id,
            source_path=Path(source.source_path),
            source_sha256=source.sha256,
            config_version_id=job.config_version_id,
            config=config,
            artifacts=LocalArtifactStore(),
        )
        parsed = ParserRegistry().parse(context)
        if job.options.get("benchmark_only"):
            from docflow.db.models import GoldenSample

            original_source = aliased(SourceFile)
            selected_pages = set(
                db.scalars(
                    select(GoldenSample.page_number)
                    .join(original_source, original_source.id == GoldenSample.source_file_id)
                    .where(original_source.sha256 == source.sha256)
                )
            )
            parsed.pages = [page for page in parsed.pages if page.page_number in selected_pages]
            if not parsed.pages:
                raise ParserError("该文件没有匹配的 Golden Set 页面")
        title = infer_title(parsed.pages, Path(source.file_name).stem)
        full_text = "\n".join(page.text for page in parsed.pages)
        document_number = infer_document_number(full_text, source.file_name)
        role = infer_document_role(source.relative_path, title)
        version_role, authority_score = infer_version_role(source.relative_path)
        visual_ready = _apply_multimodal(db, job, source, document_id, parsed.pages, config)
        normalized = NormalizedDocumentV1(
            document_id=document_id,
            source_file_id=source.id,
            source_sha256=source.sha256,
            title=title,
            document_number=document_number,
            document_role=role,
            version_role=version_role,
            case_id=stable_case_id(source),
            parser_route=parsed.parser_route,
            parser_version=parsed.parser_version,
            config_version_id=job.config_version_id,
            pages=parsed.pages,
            metadata=parsed.metadata,
            warnings=parsed.warnings,
        )
        normalized.metadata["artifact_manifest"] = write_document_artifacts(normalized)
        document, chunks = _store_document(db, source, job, normalized, authority_score)
        # Multimodal review tasks are created before Document persistence. Link
        # them only after the parent row is guaranteed to exist.
        pending_reviews = list(
            db.scalars(
                select(ReviewTask).where(
                    ReviewTask.job_id == job.id,
                    ReviewTask.source_file_id == source.id,
                    ReviewTask.document_id.is_(None),
                )
            )
        )
        for review in pending_reviews:
            if (review.details or {}).get("attempted_document_id") == document.id:
                review.document_id = document.id
                db.add(review)
        LocalArtifactStore().write_json(
            f"{document_id}/normalized-document.json",
            normalized.model_dump(mode="json"),
        )
        _index_chunks(db, job, config, chunks)
        if not visual_ready:
            source.status = SourceStatus.WAITING_REVIEW.value
        else:
            # INDEXED means every index required by this immutable config is ready.
            # A disabled text embedding route is intentional, not an incomplete state;
            # visual-only M1 jobs must therefore be publishable as well.
            source.status = SourceStatus.INDEXED.value
        db.add(source)
        db.commit()
    except Exception as exc:
        db.rollback()
        delete_visual_document_points(config, job.index_generation_id, document_id)
        source = db.get(SourceFile, source.id)
        source.status = SourceStatus.FAILED.value
        source.status_reason = str(exc)[:1000]
        # The document transaction was rolled back, so the failure task must not
        # reference a document row that does not exist.
        _review(
            db,
            job=job,
            source=source,
            document_id=None,
            category="PARSE_FAILED",
            summary="文档解析或入库失败",
            details={"reason": str(exc)[:1000], "attempted_document_id": document_id},
            severity="ERROR",
        )
        db.add(source)
        db.commit()


def process_job(db: Session, job: IngestionJob) -> IngestionJob:
    if job.status == JobStatus.QUEUED.value:
        transition_job(db, job, JobStatus.RUNNING)
    report = inventory_job(db, job)
    write_inventory_report(report)
    if job.options.get("inventory_only"):
        return transition_job(db, job, JobStatus.SUCCEEDED)

    query = select(SourceFile).where(
        SourceFile.job_id == job.id, SourceFile.status == SourceStatus.READY.value
    )
    if job.options.get("benchmark_only"):
        from docflow.db.models import GoldenSample

        original_source = aliased(SourceFile)
        golden_hashes = (
            select(original_source.sha256)
            .join(GoldenSample, GoldenSample.source_file_id == original_source.id)
            .where(original_source.sha256.is_not(None))
        )
        query = query.where(SourceFile.sha256.in_(golden_hashes)).distinct()
    for source in db.scalars(query.order_by(SourceFile.relative_path)):
        refreshed = db.get(IngestionJob, job.id)
        if refreshed.status == JobStatus.CANCELED.value:
            return refreshed
        process_source(db, refreshed, source)
        refresh_job_counts(db, refreshed)

    job = db.get(IngestionJob, job.id)
    refresh_job_counts(db, job)
    open_reviews = db.scalar(
        select(ReviewTask.id)
        .where(ReviewTask.job_id == job.id, ReviewTask.status == "OPEN")
        .limit(1)
    )
    if job.job_type == "BENCHMARK" and int(job.cloud_usage.get("calls", 0)) > 0:
        return transition_job(db, job, JobStatus.WAITING_COST_CONFIRMATION)
    if open_reviews:
        return transition_job(db, job, JobStatus.WAITING_REVIEW)
    failed = job.stage_counts.get(SourceStatus.FAILED.value, 0)
    if failed:
        return transition_job(
            db,
            job,
            JobStatus.FAILED,
            error_code="SOURCE_FAILURES",
            error_message=f"{failed} 个文件失败",
        )
    return transition_job(db, job, JobStatus.SUCCEEDED)
