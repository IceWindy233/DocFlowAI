from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from docflow.domain.config import RuntimeConfigBundleV1
from docflow.domain.documents import PageV1
from docflow.services.storage import ArtifactStore


class ParserError(RuntimeError):
    pass


class ParserUnavailable(ParserError):
    pass


@dataclass
class ParseContext:
    document_id: str
    source_file_id: str
    source_path: Path
    source_sha256: str
    config_version_id: str
    config: RuntimeConfigBundleV1
    artifacts: ArtifactStore


@dataclass
class ParsedContent:
    pages: list[PageV1]
    parser_route: str
    parser_version: str
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


class DocumentParser(Protocol):
    name: str
    supported_extensions: set[str]

    def parse(self, context: ParseContext) -> ParsedContent: ...
