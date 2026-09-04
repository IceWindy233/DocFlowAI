"""公文文体基线统计。

从本机语料目录离线统计各文种的文体区间：逐篇度量只在进程内进行，落地的只有
p25/中位/p75 与有效样本数，不保留任何逐字原文。语料目录不进仓库，进仓库的只有
这段统计逻辑，因此换一批语料重跑即可，无需分发任何样本。

本模块不依赖数据库，全部是纯函数；写入运行配置由 `docflow style-baseline --apply` 负责。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from docflow.domain.config import GenreStyleBaseline, StyleMetric
from docflow.services.style_metrics import measure_style, quantiles

DEFAULT_PATTERNS: tuple[str, ...] = ("*.doc", "*.docx", "*.txt", "*.md")
TEXT_SUFFIXES: tuple[str, ...] = (".txt", ".md")
# Office 编辑期临时文件与正文同名同目录，计入会把同一篇算两遍。
TEMP_FILE_PREFIX = "~$"
# 空白模版没有正文，计入会把字数与句长区间整体拉低。
TEMPLATE_NAME_MARKER = "稿纸"
CONVERT_TIMEOUT_SECONDS = 60
# 转换器与 Windows 侧文本常带 BOM，utf-8-sig 顺手剥掉，否则首行会多一个字符。
TEXT_ENCODING = "utf-8-sig"


def collect_documents(
    root: Path,
    patterns: tuple[str, ...] = DEFAULT_PATTERNS,
    excludes: tuple[str, ...] = (),
) -> list[Path]:
    """递归收集待统计文件，跳过临时文件与空白模版。返回顺序稳定，便于复现。

    `excludes` 按路径片段排除：同一份公文常同时存在多种格式，附件与协议正本
    也混在同一目录树里，计入会污染该文种的区间。
    """
    found: set[Path] = set()
    for pattern in patterns:
        for path in root.rglob(pattern):
            if not path.is_file():
                continue
            if path.name.startswith(TEMP_FILE_PREFIX) or TEMPLATE_NAME_MARKER in path.name:
                continue
            if any(marker in str(path) for marker in excludes):
                continue
            found.add(path)
    return sorted(found)


def _run(command: list[str]) -> bytes | None:
    """子进程一律带超时，失败返回 None：单篇转换失败不能中断整批统计。"""
    try:
        process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=CONVERT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return process.stdout if process.returncode == 0 else None


def _textutil_text(path: Path) -> str | None:
    """macOS 自带转换器，对老式 .doc 也有效，且不需要额外安装。"""
    executable = shutil.which("textutil")
    if not executable:
        return None
    payload = _run([executable, "-convert", "txt", "-stdout", str(path)])
    if payload is None:
        return None
    text = payload.decode(TEXT_ENCODING, errors="replace")
    return text if text.strip() else None


def _soffice_text(path: Path) -> str | None:
    executable = shutil.which("soffice")
    if not executable:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="docflow-style-baseline-") as temp:
            command = [
                executable,
                "--headless",
                "--convert-to",
                "txt:Text",
                "--outdir",
                temp,
                str(path),
            ]
            if _run(command) is None:
                return None
            converted = Path(temp) / f"{path.stem}.txt"
            if not converted.exists():
                return None
            return converted.read_text(encoding=TEXT_ENCODING, errors="replace")
    except OSError:
        return None


def extract_text(path: Path) -> str | None:
    """抽出正文纯文本；没有可用转换器或转换失败时返回 None。

    纯文本直接读；二进制文档先用 `textutil`，不可用或失败再退到 LibreOffice。
    """
    if path.suffix.lower() in TEXT_SUFFIXES:
        try:
            return path.read_text(encoding=TEXT_ENCODING, errors="replace")
        except OSError:
            return None
    return _textutil_text(path) or _soffice_text(path)


def build_baseline(
    texts: list[str],
    *,
    source_label: str,
    min_chars: int = 80,
) -> GenreStyleBaseline | None:
    """把逐篇实测值聚成四分位区间；有效样本为 0 时返回 None。

    篇幅低于 `min_chars` 的一律丢弃：扫描产物与空页量出来的是噪声，不是文体。
    """
    samples: dict[StyleMetric, list[float]] = {metric: [] for metric in StyleMetric}
    valid = 0
    for text in texts:
        metrics = measure_style(text).metrics
        if metrics.get(StyleMetric.CHARS, 0.0) < min_chars:
            continue
        valid += 1
        for metric, values in samples.items():
            values.append(metrics.get(metric, 0.0))
    if not valid:
        return None
    return GenreStyleBaseline(
        sample_size=valid,
        source_label=source_label,
        metrics={metric: quantiles(values) for metric, values in samples.items()},
    )
