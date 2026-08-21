from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x0: float
    y0: float
    x1: float
    y1: float
    coordinate_space: Literal["pixel", "normalized", "pdf_point"] = "normalized"


class TableCellV1(BaseModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    text: str = ""
    is_header: bool = False
    bbox: BoundingBox | None = None


class TableV1(BaseModel):
    table_id: str
    cells: list[TableCellV1]
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    html: str
    serialized_text: str
    bbox: BoundingBox | None = None
    screenshot_path: str | None = None
    complex: bool = False
    confidence: float = Field(default=0.0, ge=0, le=1)


class PageV1(BaseModel):
    page_id: str
    page_number: int = Field(ge=1)
    page_type: Literal["TEXT", "SCAN", "TABLE", "MIXED", "STAMPED", "EMPTY"] = "TEXT"
    text: str = ""
    markdown: str = ""
    headings: list[str] = Field(default_factory=list)
    tables: list[TableV1] = Field(default_factory=list)
    image_regions: list[dict[str, Any]] = Field(default_factory=list)
    image_path: str | None = None
    parser_route: str
    quality_score: float = Field(default=0.0, ge=0, le=1)
    quality_signals: dict[str, float | int | str | bool] = Field(default_factory=dict)
    visual_required: bool = False
    visual_status: Literal["NOT_REQUIRED", "PENDING", "READY", "FAILED", "REVIEW"] = "NOT_REQUIRED"


class NormalizedDocumentV1(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    document_id: str
    source_file_id: str
    source_sha256: str
    title: str = ""
    document_number: str | None = None
    document_role: str = "UNKNOWN"
    version_role: str = "UNKNOWN"
    case_id: str
    parser_route: str
    parser_version: str
    config_version_id: str
    pages: list[PageV1]
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ChunkV1(BaseModel):
    chunk_id: str
    document_id: str
    page_id: str
    ordinal: int
    kind: Literal["paragraph", "table", "heading", "mixed"] = "paragraph"
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PageVisualEmbeddingV1(BaseModel):
    page_id: str
    document_id: str
    model_signature: str
    image_sha256: str
    vectors: list[list[float]]
    vector_count: int
    dimension: int
