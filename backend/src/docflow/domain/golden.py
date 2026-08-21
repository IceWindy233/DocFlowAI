from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

GoldenStatus = Literal["PENDING", "ANNOTATED", "APPROVED", "REJECTED"]


class GoldenExpectedV1(BaseModel):
    text: str = ""
    title: str | None = None
    document_number: str | None = None
    document_role: str | None = None
    version_role: str | None = None
    page_type: str | None = None
    numeric_fields: dict[str, str] = Field(default_factory=dict)
    table_data: dict[str, Any] = Field(default_factory=dict)
    layout_elements: list[dict[str, Any]] = Field(default_factory=list)
    visual_queries: list[str] = Field(default_factory=list)


class GoldenAnnotationV1(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    status: GoldenStatus = "PENDING"
    expected: GoldenExpectedV1 = Field(default_factory=GoldenExpectedV1)
    notes: str = ""
    reviewer: str = "local-admin"
    updated_at: str | None = None
    approved_at: str | None = None
    rejection_reason: str | None = None
    replacement_history: list[dict[str, Any]] = Field(default_factory=list)


class GoldenAnnotationUpdate(BaseModel):
    expected: GoldenExpectedV1
    notes: str = Field(default="", max_length=5000)
    reviewer: str = Field(default="local-admin", min_length=1, max_length=128)


class GoldenReviewRequest(BaseModel):
    reviewer: str = Field(default="local-admin", min_length=1, max_length=128)
    reason: str = Field(default="", max_length=2000)
