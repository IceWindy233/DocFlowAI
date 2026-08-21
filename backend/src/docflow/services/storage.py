from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from docflow.core.settings import get_settings


class ArtifactStore(Protocol):
    def write_bytes(self, key: str, content: bytes) -> str: ...

    def write_json(self, key: str, content: dict[str, Any]) -> str: ...

    def read_bytes(self, key: str) -> bytes: ...


class LocalArtifactStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or get_settings().artifact_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if self.root not in candidate.parents and candidate != self.root:
            raise ValueError("非法产物路径")
        return candidate

    def write_bytes(self, key: str, content: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)

    def write_json(self, key: str, content: dict[str, Any]) -> str:
        return self.write_bytes(
            key,
            json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8"),
        )

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()
