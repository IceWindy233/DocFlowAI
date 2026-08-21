from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class RetrievalSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    mode: Literal["hybrid", "visual", "text"] = "hybrid"
    limit: int = Field(default=10, ge=1, le=50)
    index_generation_id: str | None = Field(default=None, max_length=96)
    case_ids: list[str] = Field(default_factory=list, max_length=20)
    document_roles: list[str] = Field(default_factory=list, max_length=20)
    version_roles: list[str] = Field(default_factory=list, max_length=20)
    date_from: date | None = None
    date_to: date | None = None
    min_authority_score: float | None = Field(default=None, ge=0, le=1)
    authoritative_only: bool = False
    rerank: bool = True
    debug: bool = False


class RetrievalAnswerRequest(RetrievalSearchRequest):
    evidence_limit: int = Field(default=4, ge=1, le=10)
