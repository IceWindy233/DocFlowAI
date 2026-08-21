from __future__ import annotations

import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from docflow.domain.documents import PageV1, TableCellV1, TableV1

DOCUMENT_NUMBER_PATTERN = re.compile(
    r"(?P<prefix>[\u4e00-\u9fffA-Za-z]{1,12})\s*"
    r"[〔【［\[（(]\s*(?P<year>\d{4})\s*[〕】］\]）)]?\s*"
    r"(?P<serial>\d{1,5})\s*号"
)

TITLE_END_PATTERN = re.compile(
    r"(?:请示|报告|通知|通报|公告|决定|意见函|征求意见函|复函|函|批复|"
    r"会议纪要|纪要|方案|办法|规定|申请)$"
)
FIELD_LABEL_PATTERN = re.compile(
    r"^(?:主送单位|抄送单位|发文单位|主办单位|承办单位|联系人|联系电话|"
    r"文件编号|文号|发文字号|收文日期|签发人|总经理签发|内容摘要|附件)\s*[：:]"
)


def stable_page_id(document_id: str, page_number: int) -> str:
    return f"{document_id}_p{page_number:04d}"


def quality_score(text: str) -> tuple[float, dict[str, float | int]]:
    stripped = text.strip()
    if not stripped:
        return 0.0, {"char_count": 0, "printable_ratio": 0.0, "replacement_count": 0}
    printable = sum(char.isprintable() for char in stripped)
    replacements = stripped.count("�") + stripped.count("□")
    printable_ratio = printable / len(stripped)
    replacement_ratio = replacements / len(stripped)
    length_score = min(1.0, len(stripped) / 120)
    score = max(0.0, min(1.0, 0.55 * printable_ratio + 0.35 * length_score - replacement_ratio))
    return round(score, 4), {
        "char_count": len(stripped),
        "printable_ratio": round(printable_ratio, 4),
        "replacement_count": replacements,
    }


def classify_page(text: str, tables: list[TableV1], image_only: bool = False) -> str:
    if image_only or len(text.strip()) < 20:
        return "SCAN"
    if any(table.complex for table in tables):
        return "TABLE"
    if tables:
        return "MIXED"
    if re.search(r"印章|（盖章）|公章", text):
        return "STAMPED"
    return "TEXT"


def table_from_rows(table_id: str, rows: list[list[str]]) -> TableV1:
    column_count = max((len(row) for row in rows), default=0)
    cells: list[TableCellV1] = []
    html_rows: list[str] = []
    for row_index, row in enumerate(rows):
        html_cells: list[str] = []
        for column_index in range(column_count):
            value = row[column_index] if column_index < len(row) else ""
            is_header = row_index == 0
            cells.append(
                TableCellV1(
                    row=row_index,
                    column=column_index,
                    text=value,
                    is_header=is_header,
                )
            )
            tag = "th" if is_header else "td"
            html_cells.append(f"<{tag}>{html.escape(value)}</{tag}>")
        html_rows.append(f"<tr>{''.join(html_cells)}</tr>")
    serialized = "\n".join(
        "；".join(
            f"{rows[0][index] if rows and index < len(rows[0]) else f'列{index + 1}'}：{value}"
            for index, value in enumerate(row)
        )
        for row in rows[1:]
        if any(value.strip() for value in row)
    )
    complex_table = (
        len(rows) >= 8
        or column_count >= 6
        or any(len(value) > 100 for row in rows for value in row)
    )
    return TableV1(
        table_id=table_id,
        cells=cells,
        row_count=len(rows),
        column_count=column_count,
        html=f"<table>{''.join(html_rows)}</table>",
        serialized_text=serialized,
        complex=complex_table,
        confidence=0.9,
    )


def render_pdf_page(pdf_path: Path, page_number: int, output_prefix: Path, dpi: int) -> Path | None:
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "pdftoppm",
        "-f",
        str(page_number),
        "-l",
        str(page_number),
        "-singlefile",
        "-png",
        "-r",
        str(dpi),
        str(pdf_path),
        str(output_prefix),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return None
    output = output_prefix.with_suffix(".png")
    return output if output.exists() else None


def render_quicklook_thumbnail(source_path: Path, output: Path, size: int = 1800) -> Path | None:
    executable = Path("/usr/bin/qlmanage")
    if not executable.is_file():
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="docflow-quicklook-") as temp:
            process = subprocess.run(
                [str(executable), "-t", "-s", str(size), "-o", temp, str(source_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            candidates = list(Path(temp).glob("*.png"))
            if process.returncode == 0 and candidates:
                shutil.copyfile(candidates[0], output)
                return output
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def _compact_title(parts: list[str]) -> str:
    return re.sub(r"\s+", "", "".join(parts)).strip("、，,。；;：:—-_")


def _title_from_file_stem(stem: str) -> str | None:
    candidate = stem.strip()
    match = DOCUMENT_NUMBER_PATTERN.search(candidate)
    if match:
        candidate = candidate[match.end() :]
    candidate = candidate.strip(" 、，,。；;：:—-_()（）[]【】")
    if 4 <= len(candidate) <= 120 and TITLE_END_PATTERN.search(candidate):
        return candidate
    return None


def infer_title(pages: list[PageV1], fallback: str) -> str:
    """Infer a Chinese official-document title without mistaking the issuer for it.

    File names and explicit ``文件标题`` fields are more reliable than the first OCR
    line.  Wrapped title lines are joined because official documents commonly place
    long titles on two centered lines.
    """

    fallback_title = _title_from_file_stem(fallback)
    if fallback_title:
        return fallback_title

    for page in pages[:2]:
        lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        for index, line in enumerate(lines[:20]):
            labelled = re.match(r"^文件标题\s*[：:]\s*(.*)$", line)
            if not labelled:
                continue
            parts = [labelled.group(1)] if labelled.group(1) else []
            for continuation in lines[index + 1 : index + 5]:
                if FIELD_LABEL_PATTERN.match(continuation):
                    break
                parts.append(continuation)
                if TITLE_END_PATTERN.search(_compact_title(parts)):
                    break
            candidate = _compact_title(parts)
            if 4 <= len(candidate) <= 120:
                return candidate

    for page in pages[:2]:
        lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        for index, line in enumerate(lines[:16]):
            if not (line.startswith("关于") or TITLE_END_PATTERN.search(line)):
                continue
            parts = [line]
            for continuation in lines[index + 1 : index + 4]:
                candidate = _compact_title(parts)
                if TITLE_END_PATTERN.search(candidate):
                    break
                if FIELD_LABEL_PATTERN.match(continuation) or DOCUMENT_NUMBER_PATTERN.search(
                    continuation
                ):
                    break
                parts.append(continuation)
            candidate = _compact_title(parts)
            if 4 <= len(candidate) <= 120 and TITLE_END_PATTERN.search(candidate):
                return candidate

    for page in pages:
        lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        for line in lines[:8]:
            if 4 <= len(line) <= 80 and not DOCUMENT_NUMBER_PATTERN.search(line):
                return line
    return fallback


def _canonical_document_number(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}〔{match.group('year')}〕{match.group('serial')}号"


def infer_document_number(text: str, source_name: str | None = None) -> str | None:
    """Prefer the file name and explicit number fields over references in the body."""

    if source_name:
        match = DOCUMENT_NUMBER_PATTERN.search(source_name)
        if match:
            return _canonical_document_number(match)

    labelled = re.search(r"(?:文件编号|发文字号|文号)\s*[：:]\s*([^\n]{1,40})", text)
    if labelled:
        match = DOCUMENT_NUMBER_PATTERN.search(labelled.group(1))
        if match:
            return _canonical_document_number(match)

    match = DOCUMENT_NUMBER_PATTERN.search(text)
    return _canonical_document_number(match) if match else None


def infer_document_role(path_text: str, title: str) -> str:
    text = f"{path_text} {title}"
    patterns = (
        # Reply documents often contain the original request/letter title in
        # their path. Match the more specific reply semantics before REQUEST
        # and LETTER so "关于……征求意见函的复函" is not misclassified.
        ("REPLY", r"批复|答复|回复(?:意见)?|复函|回函|函复"),
        ("REQUEST", r"请示|申请|报请"),
        ("LETTER", r"函"),
        ("NOTICE", r"通知|通告"),
        ("MEETING", r"会议|纪要|会审"),
        ("ATTACHMENT", r"附件|附表|清单"),
    )
    for role, pattern in patterns:
        if re.search(pattern, text):
            return role
    return "UNKNOWN"


def infer_version_role(path_text: str) -> tuple[str, float]:
    patterns = (
        ("FORMAL", 0.95, r"正式|定稿|盖章|印发"),
        ("REVIEW", 0.70, r"送审|审核|会签"),
        ("DRAFT", 0.35, r"草稿|初稿|修改稿|征求意见稿"),
        ("REPLY", 0.90, r"批复|回复|复函"),
    )
    for role, score, pattern in patterns:
        if re.search(pattern, path_text):
            return role, score
    suffix = Path(path_text).suffix.lower()
    if "公文稿纸" in path_text:
        return "REVIEW", 0.7
    if suffix == ".pdf":
        return "FORMAL", 0.85
    if suffix in {".doc", ".docx", ".wps"}:
        return "DRAFT", 0.45
    return "UNKNOWN", 0.5
