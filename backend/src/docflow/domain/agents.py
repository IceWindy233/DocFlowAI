from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReviewScope = Literal["STRUCTURE", "FORMAT", "FACT", "CITATION", "VERSION", "LANGUAGE", "SENSITIVE"]


class DocumentReviewCreate(BaseModel):
    document_id: str | None = Field(default=None, max_length=64)
    title: str = Field(default="待审核公文", max_length=500)
    text: str = Field(default="", max_length=120000)
    scope: list[ReviewScope] = Field(
        default_factory=lambda: [
            "STRUCTURE",
            "FORMAT",
            "FACT",
            "CITATION",
            "VERSION",
            "LANGUAGE",
            "SENSITIVE",
        ]
    )

    @model_validator(mode="after")
    def require_input(self) -> "DocumentReviewCreate":
        if not self.document_id and len(self.text.strip()) < 10:
            raise ValueError("请选择知识库文档或输入至少 10 个字符的待审核文本")
        return self


class FindingResolveRequest(BaseModel):
    action: Literal["ACCEPT", "REJECT"]
    feedback: str = Field(default="", max_length=2000)


class ReviewApplyRequest(BaseModel):
    accepted_finding_ids: list[str] = Field(default_factory=list, max_length=500)


class DraftRequirements(BaseModel):
    document_type: Literal["REQUEST", "LETTER"]
    subject: str = Field(min_length=2, max_length=500)
    recipient: str = Field(default="", max_length=500)
    background: str = Field(default="", max_length=8000)
    facts: str = Field(default="", max_length=12000)
    requested_action: str = Field(default="", max_length=8000)
    sender: str = Field(default="", max_length=500)
    date: str = Field(default="", max_length=100)
    reference_query: str = Field(default="", max_length=500)


class DraftRequirementsState(BaseModel):
    """Partial, conversation-friendly requirement state.

    Unlike DraftRequirements, every field is optional/empty while a user is
    still answering follow-up questions. It is never used directly to start
    generation until DraftRequirements validation succeeds.
    """

    model_config = ConfigDict(extra="forbid")

    document_type: Literal["REQUEST", "LETTER"] = "REQUEST"
    subject: str = Field(default="", max_length=500)
    recipient: str = Field(default="", max_length=500)
    background: str = Field(default="", max_length=8000)
    facts: str = Field(default="", max_length=12000)
    requested_action: str = Field(default="", max_length=8000)
    sender: str = Field(default="", max_length=500)
    date: str = Field(default="", max_length=100)
    reference_query: str = Field(default="", max_length=500)


class DraftConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class DraftInterpretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=8000)
    current_requirements: DraftRequirementsState = Field(default_factory=DraftRequirementsState)
    history: list[DraftConversationTurn] = Field(default_factory=list, max_length=12)


class DraftCreateRequest(BaseModel):
    requirements: DraftRequirements


class DraftOutlineUpdate(BaseModel):
    outline: list[dict[str, str | bool]] = Field(min_length=1, max_length=20)


class DraftTextUpdate(BaseModel):
    draft_text: str = Field(min_length=20, max_length=120000)


class DraftRegenerateRequest(BaseModel):
    mode: Literal["FULL", "SECTION", "PRESERVE_MANUAL"] = "FULL"
    section_id: str | None = Field(default=None, max_length=100)
    instruction: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def require_section(self) -> "DraftRegenerateRequest":
        if self.mode == "SECTION" and not self.section_id:
            raise ValueError("局部重新生成必须指定 section_id")
        return self
