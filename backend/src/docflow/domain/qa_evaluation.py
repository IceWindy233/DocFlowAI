from typing import Literal

from pydantic import BaseModel, Field


class QaEvaluationGenerateRequest(BaseModel):
    target_count: int = Field(default=20, ge=5, le=100)
    replace: bool = False


class QaEvaluationSampleUpdate(BaseModel):
    question: str | None = Field(default=None, min_length=2, max_length=500)
    reference_answer: str | None = Field(default=None, min_length=1, max_length=4000)
    answer_aliases: list[str] | None = Field(default=None, max_length=20)
    expected_page_ids: list[str] | None = Field(default=None, max_length=20)
    status: Literal["DRAFT", "CONFIRMED", "DISABLED"] | None = None
    notes: str | None = Field(default=None, max_length=2000)


class QaEvaluationRunRequest(BaseModel):
    mode: Literal["RETRIEVAL_ONLY", "FULL_QA"] = "FULL_QA"
    sample_ids: list[str] = Field(default_factory=list, max_length=100)
