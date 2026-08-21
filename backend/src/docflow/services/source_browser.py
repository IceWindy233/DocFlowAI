from __future__ import annotations

from pathlib import Path
from typing import Any


class SourceDirectoryError(ValueError):
    """Raised when a requested source directory cannot be browsed safely."""


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def browse_source_directories(root: Path, requested: str | None = None) -> dict[str, Any]:
    allowed_root = root.expanduser().resolve()
    if not allowed_root.is_dir():
        raise SourceDirectoryError(f"数据源根目录不存在：{allowed_root}")

    if requested:
        raw_path = Path(requested).expanduser()
        current = (raw_path if raw_path.is_absolute() else allowed_root / raw_path).resolve()
    else:
        current = allowed_root

    if not _is_within(current, allowed_root):
        raise SourceDirectoryError("不能浏览数据源根目录之外的路径")
    if not current.is_dir():
        raise SourceDirectoryError(f"目录不存在：{current}")

    directories: list[dict[str, str]] = []
    try:
        children = sorted(current.iterdir(), key=lambda item: item.name.casefold())
    except OSError as exc:
        raise SourceDirectoryError(f"无法读取目录：{current}") from exc

    for child in children:
        try:
            resolved = child.resolve()
            if child.is_dir() and _is_within(resolved, allowed_root):
                directories.append({"name": child.name, "path": str(resolved)})
        except OSError:
            continue

    parent = None
    if current != allowed_root and _is_within(current.parent, allowed_root):
        parent = str(current.parent)

    return {
        "root": str(allowed_root),
        "current": str(current),
        "parent": parent,
        "directories": directories,
    }
