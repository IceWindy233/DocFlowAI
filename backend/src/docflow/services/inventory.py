from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pypdf import PdfReader
from rarfile import Error as RarError
from rarfile import RarFile
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from docflow.core.settings import get_settings
from docflow.db.models import IngestionJob, SourceFile, SourceStatus
from docflow.domain.config import RuntimeConfigBundleV1
from docflow.services.config_service import get_version
from docflow.services.jobs import job_source_roots, refresh_job_counts

logging.getLogger("pypdf").setLevel(logging.ERROR)

SUPPORTED_EXTENSIONS = {
    ".doc",
    ".docx",
    ".wps",
    ".pdf",
    ".xls",
    ".xlsx",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
}
ARCHIVE_EXTENSIONS = {".zip", ".rar"}
IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "data",
    "models",
    "backend",
    "frontend",
    "infra",
    "scripts",
    "docs",
}


def is_temporary_file(path: Path) -> bool:
    name = path.name
    lower = name.lower()
    return (
        name.startswith("~$")
        or lower.endswith((".tmp", ".temp", ".part", ".crdownload"))
        or lower in {".ds_store", "thumbs.db", "desktop.ini"}
    )


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(block_size):
            digest.update(chunk)
    return digest.hexdigest()


def detect_mime(path: Path) -> str:
    with path.open("rb") as stream:
        header = stream.read(16)
    if header.startswith(b"%PDF"):
        return "application/pdf"
    if header.startswith(b"PK\x03\x04"):
        if path.suffix.lower() == ".docx":
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if path.suffix.lower() == ".xlsx":
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return "application/zip"
    if header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "application/x-ole-storage"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def safe_archive_entries(path: Path, config: RuntimeConfigBundleV1) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    total_uncompressed = 0
    limit_bytes = config.parsing.archive_max_uncompressed_mb * 1024 * 1024
    try:
        archive_class = ZipFileAdapter if path.suffix.lower() == ".zip" else RarFileAdapter
        with archive_class(path) as archive:
            for info in archive.infolist()[: config.parsing.archive_max_entries]:
                pure = PurePosixPath(info.filename)
                unsafe = pure.is_absolute() or ".." in pure.parts
                depth = len([part for part in pure.parts if part not in {"", "."}])
                total_uncompressed += info.file_size
                entries.append(
                    {
                        "path": info.filename,
                        "size_bytes": info.file_size,
                        "compressed_bytes": info.compress_size,
                        "unsafe": unsafe,
                        "depth": depth,
                        "eligible": (
                            not unsafe
                            and depth <= config.parsing.archive_max_depth
                            and total_uncompressed <= limit_bytes
                        ),
                    }
                )
    except (OSError, zipfile.BadZipFile, RarError) as exc:
        return [{"status": "FAILED", "reason": str(exc)[:300]}]
    return entries


class ZipFileAdapter:
    def __init__(self, path: Path) -> None:
        self.archive = zipfile.ZipFile(path)

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        self.archive.close()

    def infolist(self):
        return self.archive.infolist()


class RarInfoAdapter:
    def __init__(self, info: Any) -> None:
        self.filename = info.filename
        self.file_size = info.file_size
        self.compress_size = info.compress_size


class RarFileAdapter:
    def __init__(self, path: Path) -> None:
        self.archive = RarFile(path)

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        self.archive.close()

    def infolist(self):
        return [RarInfoAdapter(info) for info in self.archive.infolist()]


def estimate_page_count(path: Path, mime_type: str) -> int | None:
    if mime_type != "application/pdf":
        return None
    try:
        return len(PdfReader(str(path), strict=False).pages)
    except Exception:
        return None


def infer_case_hint(relative_path: Path) -> str | None:
    parts = relative_path.parts
    if len(parts) < 2:
        return None
    parent = relative_path.parent.name
    return parent or None


def _iter_source_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in IGNORED_DIRECTORY_NAMES]
        current_path = Path(current)
        paths.extend(current_path / name for name in filenames)
    return sorted(paths, key=lambda item: str(item).lower())


def _source_root_labels(roots: list[Path]) -> dict[Path, str]:
    """Build readable, collision-free prefixes for a multi-root inventory."""
    labels: dict[Path, str] = {}
    used: set[str] = set()
    for index, root in enumerate(roots, start=1):
        base = root.name or f"数据源{index}"
        label = base
        suffix = 2
        while label.casefold() in used:
            label = f"{base}-{suffix}"
            suffix += 1
        labels[root] = label
        used.add(label.casefold())
    return labels


def inventory_job(
    db: Session, job: IngestionJob, *, replace_existing: bool = False
) -> dict[str, Any]:
    roots = job_source_roots(job)
    for root in roots:
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"数据源目录不存在：{root}")
    config_version = get_version(db, job.config_version_id)
    config = RuntimeConfigBundleV1.model_validate(config_version.content)

    existing_count = db.query(SourceFile).filter(SourceFile.job_id == job.id).count()
    if existing_count and not replace_existing:
        refresh_job_counts(db, job)
        return build_inventory_report(db, job)
    if replace_existing:
        db.execute(delete(SourceFile).where(SourceFile.job_id == job.id))
        db.commit()

    canonical_by_hash: dict[str, str] = {}
    batch: list[SourceFile] = []
    labels = _source_root_labels(roots)
    for root in roots:
        for path in _iter_source_files(root):
            local_relative = path.relative_to(root)
            relative = (
                Path(labels[root]) / local_relative if len(roots) > 1 else local_relative
            )
            try:
                stat = path.stat()
                extension = path.suffix.lower()
                temp = is_temporary_file(path)
                digest = None if temp else sha256_file(path)
                mime_type = detect_mime(path)
                archive_entries: list[dict[str, Any]] = []
                canonical_id = canonical_by_hash.get(digest) if digest else None
                if temp:
                    status = SourceStatus.SKIPPED_TEMP.value
                    reason = "Office 或系统临时文件"
                elif canonical_id:
                    status = SourceStatus.DUPLICATE.value
                    reason = f"与 {canonical_id} 内容相同"
                elif extension in ARCHIVE_EXTENSIONS:
                    archive_entries = safe_archive_entries(path, config)
                    status = SourceStatus.UNSUPPORTED.value
                    reason = "归档容器已完成安全枚举，不直接进入文档解析"
                elif extension not in SUPPORTED_EXTENSIONS:
                    status = SourceStatus.UNSUPPORTED.value
                    reason = f"M1 不支持格式 {extension or '(无扩展名)'}"
                elif stat.st_size > config.parsing.max_file_size_mb * 1024 * 1024:
                    status = SourceStatus.UNSUPPORTED.value
                    reason = "文件超过配置的大小上限"
                else:
                    status = SourceStatus.READY.value
                    reason = None

                item = SourceFile(
                    job_id=job.id,
                    source_path=str(path),
                    relative_path=str(relative),
                    file_name=path.name,
                    extension=extension,
                    mime_type=mime_type,
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    sha256=digest,
                    canonical_source_id=canonical_id,
                    status=status,
                    status_reason=reason,
                    case_hint=infer_case_hint(local_relative),
                    page_count=estimate_page_count(path, mime_type),
                    archive_entries=archive_entries,
                )
                db.add(item)
                db.flush()
                if digest and digest not in canonical_by_hash:
                    canonical_by_hash[digest] = item.id
                batch.append(item)
                if len(batch) >= 100:
                    db.commit()
                    batch.clear()
            except (OSError, PermissionError) as exc:
                db.add(
                    SourceFile(
                        job_id=job.id,
                        source_path=str(path),
                        relative_path=str(relative),
                        file_name=path.name,
                        extension=path.suffix.lower(),
                        status=SourceStatus.FAILED.value,
                        status_reason=str(exc)[:500],
                    )
                )
    db.commit()
    refresh_job_counts(db, job)
    return build_inventory_report(db, job)


def build_inventory_report(db: Session, job: IngestionJob) -> dict[str, Any]:
    sources = list(db.scalars(select(SourceFile).where(SourceFile.job_id == job.id)))
    status_counts = Counter(item.status for item in sources)
    extension_counts = Counter(item.extension or "(none)" for item in sources)
    mime_counts = Counter(item.mime_type for item in sources)
    total_bytes = sum(item.size_bytes for item in sources)
    duplicate_extra_bytes = sum(item.size_bytes for item in sources if item.status == "DUPLICATE")
    report = {
        "schema_version": "1.0",
        "job_id": job.id,
        "source_root": job.source_root,
        "source_roots": [str(root) for root in job_source_roots(job)],
        "config_version_id": job.config_version_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "total_files": len(sources),
            "total_bytes": total_bytes,
            "duplicate_extra_bytes": duplicate_extra_bytes,
            "status_counts": dict(sorted(status_counts.items())),
            "extension_counts": dict(sorted(extension_counts.items())),
            "mime_counts": dict(sorted(mime_counts.items())),
        },
        "terminal_coverage": (
            sum(
                count
                for status, count in status_counts.items()
                if status not in {SourceStatus.DISCOVERED.value, SourceStatus.PROCESSING.value}
            )
            / len(sources)
            if sources
            else 1.0
        ),
    }
    return report


def write_inventory_report(report: dict[str, Any]) -> Path:
    settings = get_settings()
    output = settings.report_root / f"inventory-{report['job_id']}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return output
