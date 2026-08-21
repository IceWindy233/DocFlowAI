from typing import Literal

from pydantic import BaseModel, Field, model_validator

AgentCapability = Literal["QA", "REVIEW", "DRAFT"]
AgentEvaluationMode = Literal[
    "LOCAL_RETRIEVAL",
    "FULL_QA",
    "LOCAL_RULES",
    "FULL_REVIEW",
    "REQUIREMENT_GATE",
    "FULL_DRAFT",
]

ALLOWED_MODES = {
    "QA": {"LOCAL_RETRIEVAL", "FULL_QA"},
    "REVIEW": {"LOCAL_RULES", "FULL_REVIEW"},
    "DRAFT": {"REQUIREMENT_GATE", "FULL_DRAFT"},
}


class AgentEvaluationRunRequest(BaseModel):
    capability: AgentCapability
    mode: AgentEvaluationMode
    sample_ids: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_mode(self) -> "AgentEvaluationRunRequest":
        if self.mode not in ALLOWED_MODES[self.capability]:
            raise ValueError(f"{self.capability} 不支持运行模式 {self.mode}")
        return self
