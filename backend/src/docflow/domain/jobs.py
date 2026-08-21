from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class IngestionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publish_on_success: bool = False
    cloud_processing_allowed: bool = False
    full_cloud_run_confirmed: bool = False
    force_reparse: bool = False
    inventory_only: bool = False
    benchmark_only: bool = False


class IngestionJobCreate(BaseModel):
    job_type: Literal["FULL_SCAN", "INCREMENTAL_SCAN", "REBUILD", "BENCHMARK"] = "FULL_SCAN"
    # source_root is retained for CLI/API compatibility. New Web clients should
    # submit source_roots so one immutable task can cover several directories.
    source_root: str | None = None
    source_roots: list[str] = Field(default_factory=list, max_length=32)
    options: IngestionOptions = Field(default_factory=IngestionOptions)

    @model_validator(mode="after")
    def require_source_directory(self) -> IngestionJobCreate:
        roots = [item.strip() for item in self.source_roots if item.strip()]
        if self.source_root and self.source_root.strip():
            legacy_root = self.source_root.strip()
            if legacy_root not in roots:
                roots.insert(0, legacy_root)
        if not roots:
            raise ValueError("至少选择一个数据源目录")
        self.source_roots = roots
        self.source_root = roots[0]
        return self


class IngestionJobResponse(BaseModel):
    id: str
    job_type: str
    source_root: str
    status: str
    config_version_id: str
    index_generation_id: str
    options: dict[str, Any]
    progress: dict[str, Any]
    stage_counts: dict[str, Any]
    model_signatures: dict[str, Any]
    cloud_usage: dict[str, Any]
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @computed_field
    @property
    def source_roots(self) -> list[str]:
        roots = self.options.get("source_roots")
        if isinstance(roots, list):
            values = [str(item) for item in roots if str(item).strip()]
            if values:
                return values
        return [self.source_root]

    model_config = ConfigDict(from_attributes=True)


class ReviewResolveRequest(BaseModel):
    action: Literal["ACCEPT", "REJECT", "REPARSE", "CORRECT"]
    reason: str = Field(min_length=2, max_length=1000)
    corrections: dict[str, Any] = Field(default_factory=dict)


class DocumentCorrectionRequest(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=500)
    document_number: str | None = Field(default=None, max_length=200)
    document_role: Literal[
        "REQUEST", "LETTER", "REPLY", "NOTICE", "MEETING", "ATTACHMENT", "UNKNOWN"
    ] | None = None
    version_role: Literal["DRAFT", "REVIEW", "FORMAL", "REPLY", "UNKNOWN"] | None = None
    case_id: str | None = Field(default=None, min_length=3, max_length=96)
    authority_score: float | None = Field(default=None, ge=0, le=1)
    selected: bool | None = None
    reason: str = Field(min_length=2, max_length=1000)


class ReparseRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=1000)
    cloud_processing_allowed: bool = False
    full_cloud_run_confirmed: bool = False
