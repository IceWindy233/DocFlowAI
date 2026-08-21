from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from docflow.db.models import Document, IngestionJob, SourceFile

CATEGORY_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("PDF_NATIVE", 0.15),
    ("PDF_SCANNED", 0.15),
    ("PDF_COMPLEX", 0.075),
    ("DOC", 0.20),
    ("DOCX", 0.15),
    ("WPS", 0.10),
    ("SPREADSHEET", 0.075),
    ("IMAGE", 0.005),
    ("DUPLICATE_PAIR", 0.04),
    ("SKIPPED_TEMP", 0.025),
    ("UNSUPPORTED", 0.03),
)


@dataclass(frozen=True)
class ExpansionCandidate:
    source_file_id: str
    source_path: str
    relative_path: str
    file_name: str
    extension: str
    mime_type: str
    size_bytes: int
    sha256: str | None
    inventory_status: str
    case_hint: str | None
    category: str
    corpus_group: str


def allocate_quotas(target: int) -> dict[str, int]:
    if target < 1:
        raise ValueError("样本数量必须大于 0")
    raw = [(name, target * weight) for name, weight in CATEGORY_WEIGHTS]
    quotas = {name: int(value) for name, value in raw}
    remainder = target - sum(quotas.values())
    ranked = sorted(raw, key=lambda item: (-(item[1] - int(item[1])), item[0]))
    for name, _ in ranked[:remainder]:
        quotas[name] += 1
    return quotas


def _stable_key(seed: str, candidate: ExpansionCandidate) -> str:
    value = candidate.sha256 or candidate.relative_path
    return hashlib.sha256(f"{seed}:{value}:{candidate.relative_path}".encode()).hexdigest()


def _corpus_group(relative_path: str) -> str:
    parts = Path(relative_path).parts
    return "/".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "UNKNOWN")


def _pdf_category(path: Path, size_bytes: int) -> str:
    try:
        reader = PdfReader(path)
        page_count = len(reader.pages)
        text = "".join((page.extract_text() or "") for page in reader.pages[:2])
        searchable_chars = len("".join(text.split()))
        if page_count >= 8 or size_bytes >= 5 * 1024 * 1024:
            return "PDF_COMPLEX"
        if searchable_chars < 80:
            return "PDF_SCANNED"
        return "PDF_NATIVE"
    except Exception:
        return "PDF_SCANNED"


def _category(source: SourceFile) -> str | None:
    if source.status == "SKIPPED_TEMP":
        return "SKIPPED_TEMP"
    if source.status == "UNSUPPORTED":
        return "UNSUPPORTED"
    if source.status == "DUPLICATE":
        return None
    if source.status != "READY":
        return None
    extension = source.extension.lower()
    if extension == ".pdf":
        return _pdf_category(Path(source.source_path), source.size_bytes)
    if extension == ".doc":
        return "DOC"
    if extension == ".docx":
        return "DOCX"
    if extension == ".wps":
        return "WPS"
    if extension in {".xls", ".xlsx"}:
        return "SPREADSHEET"
    if extension in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
        return "IMAGE"
    return None


def _candidate(source: SourceFile, category: str) -> ExpansionCandidate:
    return ExpansionCandidate(
        source_file_id=source.id,
        source_path=source.source_path,
        relative_path=source.relative_path,
        file_name=source.file_name,
        extension=source.extension,
        mime_type=source.mime_type,
        size_bytes=source.size_bytes,
        sha256=source.sha256,
        inventory_status=source.status,
        case_hint=source.case_hint,
        category=category,
        corpus_group=_corpus_group(source.relative_path),
    )


def _balanced_pick(
    candidates: list[ExpansionCandidate],
    count: int,
    *,
    seed: str,
    selected_paths: set[str],
    selected_hashes: set[str],
    case_counts: Counter[str],
    group_counts: Counter[str],
) -> list[ExpansionCandidate]:
    available = [
        item
        for item in candidates
        if item.source_path not in selected_paths
        and (not item.sha256 or item.sha256 not in selected_hashes)
        and Path(item.source_path).is_file()
    ]
    available.sort(key=lambda item: _stable_key(seed, item))
    picked: list[ExpansionCandidate] = []
    for case_limit in (2, 4, 10_000):
        while len(picked) < count:
            eligible = [
                item
                for item in available
                if case_counts[item.case_hint or item.relative_path] < case_limit
            ]
            if not eligible:
                break
            item = min(
                eligible,
                key=lambda value: (
                    group_counts[value.corpus_group],
                    case_counts[value.case_hint or value.relative_path],
                    _stable_key(seed, value),
                ),
            )
            available.remove(item)
            picked.append(item)
            selected_paths.add(item.source_path)
            if item.sha256:
                selected_hashes.add(item.sha256)
            case_counts[item.case_hint or item.relative_path] += 1
            group_counts[item.corpus_group] += 1
        if len(picked) >= count:
            break
    return picked


def _duplicate_pairs(
    rows: list[SourceFile],
    count: int,
    *,
    seed: str,
    excluded_hashes: set[str],
    selected_paths: set[str],
    selected_hashes: set[str],
    case_counts: Counter[str],
    group_counts: Counter[str],
) -> list[ExpansionCandidate]:
    groups: dict[str, list[SourceFile]] = defaultdict(list)
    for row in rows:
        if row.sha256 and row.sha256 not in excluded_hashes and Path(row.source_path).is_file():
            groups[row.sha256].append(row)
    pair_groups = [
        values
        for values in groups.values()
        if len(values) >= 2 and any(item.status == "DUPLICATE" for item in values)
    ]
    pair_groups.sort(
        key=lambda values: hashlib.sha256(
            f"{seed}:{values[0].sha256}".encode()
        ).hexdigest()
    )
    picked: list[ExpansionCandidate] = []
    for values in pair_groups:
        if len(picked) + 2 > count:
            break
        ordered = sorted(values, key=lambda item: (item.status == "DUPLICATE", item.relative_path))
        duplicate = next(
            item for item in ordered[1:] if item.source_path != ordered[0].source_path
        )
        pair = [ordered[0], duplicate]
        if any(item.source_path in selected_paths for item in pair):
            continue
        for source in pair:
            candidate = _candidate(source, "DUPLICATE_PAIR")
            picked.append(candidate)
            selected_paths.add(candidate.source_path)
            case_counts[candidate.case_hint or candidate.relative_path] += 1
            group_counts[candidate.corpus_group] += 1
        if pair[0].sha256:
            selected_hashes.add(pair[0].sha256)
    return picked


def _materialize(
    selected: list[ExpansionCandidate],
    output_root: Path,
    *,
    replace: bool,
) -> dict[str, str]:
    if output_root.exists() and any(output_root.iterdir()):
        if not replace:
            raise FileExistsError(f"输出目录非空，请更换目录或显式使用 --replace：{output_root}")
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    modes: dict[str, str] = {}
    for item in selected:
        source = Path(item.source_path)
        destination = output_root / item.relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, destination)
            modes[item.relative_path] = "HARDLINK"
        except OSError:
            shutil.copy2(source, destination)
            modes[item.relative_path] = "COPY"
    return modes


def prepare_expansion_sample(
    db: Session,
    *,
    inventory_job_id: str,
    output_root: Path,
    report_path: Path,
    target: int = 200,
    seed: str = "docflow-expansion-v1",
    replace: bool = False,
) -> dict[str, Any]:
    job = db.get(IngestionJob, inventory_job_id)
    if not job:
        raise LookupError(f"M0 盘点任务不存在：{inventory_job_id}")
    rows = list(db.scalars(select(SourceFile).where(SourceFile.job_id == inventory_job_id)))
    if not rows:
        raise LookupError("M0 盘点任务没有 SourceFile 记录")
    parsed_hashes = {
        value
        for value in db.scalars(
            select(SourceFile.sha256)
            .join(Document, Document.source_file_id == SourceFile.id)
            .where(SourceFile.sha256.is_not(None))
        )
        if value
    }
    quotas = allocate_quotas(target)
    by_category: dict[str, list[ExpansionCandidate]] = defaultdict(list)
    for source in rows:
        if source.sha256 and source.sha256 in parsed_hashes:
            continue
        category = _category(source)
        if category:
            by_category[category].append(_candidate(source, category))

    selected: list[ExpansionCandidate] = []
    selected_paths: set[str] = set()
    selected_hashes: set[str] = set()
    case_counts: Counter[str] = Counter()
    group_counts: Counter[str] = Counter()
    for category, _ in CATEGORY_WEIGHTS:
        if category == "DUPLICATE_PAIR":
            continue
        selected.extend(
            _balanced_pick(
                by_category.get(category, []),
                quotas[category],
                seed=f"{seed}:{category}",
                selected_paths=selected_paths,
                selected_hashes=selected_hashes,
                case_counts=case_counts,
                group_counts=group_counts,
            )
        )
    selected.extend(
        _duplicate_pairs(
            rows,
            quotas["DUPLICATE_PAIR"],
            seed=seed,
            excluded_hashes=parsed_hashes,
            selected_paths=selected_paths,
            selected_hashes=selected_hashes,
            case_counts=case_counts,
            group_counts=group_counts,
        )
    )

    if len(selected) < target:
        fallback = [
            item
            for values in by_category.values()
            for item in values
            if item.category not in {"SKIPPED_TEMP", "UNSUPPORTED"}
        ]
        selected.extend(
            _balanced_pick(
                fallback,
                target - len(selected),
                seed=f"{seed}:FALLBACK",
                selected_paths=selected_paths,
                selected_hashes=selected_hashes,
                case_counts=case_counts,
                group_counts=group_counts,
            )
        )
    if len(selected) != target:
        raise RuntimeError(f"无法选满 {target} 个样本，当前仅选出 {len(selected)} 个")

    modes = _materialize(selected, output_root.resolve(), replace=replace)
    category_counts = Counter(item.category for item in selected)
    status_counts = Counter(item.inventory_status for item in selected)
    extension_counts = Counter(item.extension for item in selected)
    report = {
        "schema_version": "1.0",
        "sample_set_id": seed,
        "inventory_job_id": inventory_job_id,
        "source_root": str(output_root.resolve()),
        "generated_at": datetime.now(UTC).isoformat(),
        "selection": {
            "target": target,
            "seed": seed,
            "excluded_previously_parsed_sha256": len(parsed_hashes),
            "requested_quotas": quotas,
            "category_counts": dict(sorted(category_counts.items())),
            "inventory_status_counts": dict(sorted(status_counts.items())),
            "extension_counts": dict(sorted(extension_counts.items())),
            "corpus_group_counts": dict(sorted(group_counts.items())),
            "unique_case_count": len(case_counts),
            "unique_sha256_count": len({item.sha256 for item in selected if item.sha256}),
            "total_bytes": sum(item.size_bytes for item in selected),
            "materialization_counts": dict(sorted(Counter(modes.values()).items())),
        },
        "items": [
            {
                **asdict(item),
                "materialization": modes[item.relative_path],
            }
            for item in sorted(selected, key=lambda value: value.relative_path)
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
