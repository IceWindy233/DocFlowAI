from sqlalchemy.orm import Session

from docflow.domain.agents import DraftInterpretRequest, DraftRequirementsState
from docflow.services.config_service import ensure_default_config
from docflow.services.draft_conversation import interpret_draft_message
from docflow.services.model_gateway import CloudModelError, StructuredGenerationResult


def test_interpret_turn_uses_model_and_preserves_explicit_facts(
    db: Session, monkeypatch
) -> None:
    ensure_default_config(db)
    calls: list[str] = []

    def fake_generation(
        *_args,
        purpose: str,
        system_prompt: str,
        payload: dict,
        **_kwargs,
    ):
        calls.append(purpose)
        assert "subject：事项名称或简短动宾短语" in system_prompt
        assert "不要机械复制 facts 中全部期限、用途、担保" in system_prompt
        assert "拟于某月办理" in system_prompt
        assert "2 至 8 个检索关键词" in system_prompt
        assert payload["current_requirements"]["background"] == ""
        return StructuredGenerationResult(
            content={
                "requirements_patch": {
                    "document_type": "REQUEST",
                    "subject": "申请流动资金贷款",
                    "recipient": "某商业银行示例分行",
                    "facts": (
                        "贷款金额500万元；期限12个月；用于园区运营周转费用；"
                        "固定资产抵押；还款来源为经营收入；拟于2026年9月办理。"
                    ),
                    "requested_action": (
                        "申请同意向某商业银行示例分行申请500万元流动资金贷款。"
                    ),
                    "sender": "某市示例产业运营有限公司",
                    "reference_query": "银行借款 流动资金贷款 请示",
                },
                "confidence": 0.96,
                "ambiguities": [],
            },
            model_signature="fake:requirement-interpreter",
            usage={"calls": 1, "input_tokens": 20, "output_tokens": 30},
        )

    monkeypatch.setattr(
        "docflow.services.draft_conversation.generate_structured_content",
        fake_generation,
    )
    result = interpret_draft_message(
        db,
        DraftInterpretRequest(
            message=(
                "请参考以往银行借款请示，为某市示例产业运营有限公司起草一份向某商业银行示例分行"
                "申请流动资金贷款的请示。贷款金额500万元，期限12个月，用于支付园区运营周转费用，"
                "担保方式为固定资产抵押，还款来源为经营收入，拟于2026年9月办理。"
            )
        ),
    )

    assert calls == ["公文需求理解"]
    assert result["requirements"]["sender"] == "某市示例产业运营有限公司"
    assert result["requirements"]["recipient"] == "某商业银行示例分行"
    assert result["requirements"]["subject"] == "申请流动资金贷款"
    assert result["requirements"]["background"] == ""
    assert result["requirements"]["date"] == ""
    assert result["requirements"]["reference_query"] == "银行借款 流动资金贷款 请示"
    assert result["requirements"]["requested_action"] == (
        "申请同意向某商业银行示例分行申请500万元流动资金贷款。"
    )
    assert result["missing_fields"] == ["背景与依据"]
    assert result["model_signature"] == "fake:requirement-interpreter"
    assert [item["node"] for item in result["trace"]] == [
        "requirement_interpreter",
        "requirement_merger",
        "missing_field_planner",
    ]


def test_interpret_turn_does_not_overwrite_confirmed_fields(
    db: Session, monkeypatch
) -> None:
    ensure_default_config(db)

    def fake_generation(*_args, **_kwargs):
        return StructuredGenerationResult(
            content={
                "requirements_patch": {"facts": "新增预算为5万元。"},
                "confidence": 0.8,
            },
            model_signature="fake:follow-up",
            usage={"calls": 1, "input_tokens": 5, "output_tokens": 5},
        )

    monkeypatch.setattr(
        "docflow.services.draft_conversation.generate_structured_content",
        fake_generation,
    )
    result = interpret_draft_message(
        db,
        DraftInterpretRequest(
            message="补充预算信息。",
            current_requirements=DraftRequirementsState(
                subject="已确认主题",
                sender="已确认发文单位",
            ),
        ),
    )
    assert result["requirements"]["subject"] == "已确认主题"
    assert result["requirements"]["sender"] == "已确认发文单位"
    assert result["requirements"]["facts"] == "新增预算为5万元。"


def test_interpret_turn_degrades_without_inventing_facts(
    db: Session, monkeypatch
) -> None:
    ensure_default_config(db)

    def unavailable(*_args, **_kwargs):
        raise CloudModelError("环境变量 DASHSCOPE_API_KEY 不可用")

    monkeypatch.setattr(
        "docflow.services.draft_conversation.generate_structured_content",
        unavailable,
    )
    result = interpret_draft_message(
        db,
        DraftInterpretRequest(message="请帮我写一份公文。"),
    )
    assert result["warning"] == "环境变量 DASHSCOPE_API_KEY 不可用"
    assert result["requirements"]["facts"] == ""
    assert result["requirements"]["background"] == ""
    assert result["missing_fields"] == [
        "事项主题",
        "主送单位",
        "背景与依据",
        "关键事实",
        "请示或函请事项",
        "发文单位",
    ]
    assert result["trace"][0]["status"] == "DEGRADED"
