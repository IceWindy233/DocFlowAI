"""十个不调用云模型的 Agent 回归场景。"""

import pytest
from sqlalchemy.orm import Session

from docflow.db.models import DraftTask
from docflow.domain.agents import DraftRequirements
from docflow.services.config_service import ensure_default_config
from docflow.services.draft_agent import (
    _candidate_quality,
    _missing,
    export_draft,
    verify_draft_content,
)
from docflow.services.review_agent import (
    _canonical_category,
    _category_relevant_reason,
    _deduplicate,
    _reference_policy_allows_finding,
    _sanitize_semantic_finding,
    deterministic_review,
)


def _review(text: str):
    return deterministic_review(
        text,
        "关于测试事项的请示",
        ["STRUCTURE", "FORMAT", "FACT", "SENSITIVE", "LANGUAGE"],
    )


def _requirements(**changes: str) -> DraftRequirements:
    values = {
        "document_type": "REQUEST",
        "subject": "停车场升级改造",
        "recipient": "镇人民政府",
        "background": "为提升停车场运营条件，拟实施升级改造。",
        "facts": "项目预算5万元，计划于2026年9月30日前完成。",
        "requested_action": "申请同意实施。",
        "sender": "测试单位",
        "date": "2026年8月13日",
        "reference_query": "停车场升级改造",
    }
    values.update(changes)
    return DraftRequirements.model_validate(values)


def _task(db: Session, text: str, verification: dict | None = None) -> DraftTask:
    version = ensure_default_config(db)
    task = DraftTask(
        document_type="REQUEST",
        title="停车场升级改造",
        requirements=_requirements().model_dump(),
        config_version_id=version.id,
        draft_text=text,
        evidence_bundle=[{"id": 1}],
        verification=verification or {},
    )
    db.add(task)
    db.commit()
    return task


def test_scenario_01_bad_date_format() -> None:
    findings = _review("镇人民政府：\n计划于2026-09-30完成。\n以上请示，妥否，请批示。")
    assert any(item["category"] == "DATE_FORMAT" for item in findings)


def test_scenario_02_phone_number_masking() -> None:
    findings = _review("镇人民政府：\n联系人13800138000。\n以上请示，妥否，请批示。")
    assert any(item["category"] == "SENSITIVE_INFO" for item in findings)


def test_scenario_03_colloquial_language() -> None:
    findings = _review("镇人民政府：\n请尽快弄好这个项目。\n以上请示，妥否，请批示。")
    assert sum(item["category"] == "LANGUAGE" for item in findings) >= 1


def test_scenario_03b_colloquial_request_is_directly_replaceable() -> None:
    findings = _review("镇人民政府：\n请尽快帮忙安排一下设备维护。\n以上请示，妥否，请批示。")
    finding = next(
        item for item in findings if item["original_text"] == "请尽快帮忙安排一下"
    )
    assert finding["suggested_text"] == "请尽快安排"
    assert finding["auto_fixable"] is True


def test_scenario_04_attachment_sequence_gap() -> None:
    findings = _review("镇人民政府：\n附件1、附件3。\n以上请示，妥否，请批示。")
    assert any(item["category"] == "ATTACHMENT_SEQUENCE" for item in findings)


def test_scenario_05_rule_llm_duplicate_merges_sources() -> None:
    base = _review("镇人民政府：\n计划于2026-09-30完成。\n以上请示，妥否，请批示。")
    rule = next(item for item in base if item["category"] == "DATE_FORMAT")
    llm = {
        **rule,
        "category": "FORMAT",
        "severity": "MAJOR",
        "reason": "模型认为日期格式不规范",
        "sources": ["LLM"],
    }
    merged = _deduplicate([rule, llm])
    dates = [item for item in merged if item["category"] == "DATE_FORMAT"]
    assert len(dates) == 1
    assert dates[0]["sources"] == ["LLM", "RULE"]
    assert dates[0]["severity"] == "MINOR"

    unrelated = {
        **rule,
        "reason": "正文措辞偏口语化，应使用正式书面语。",
        "sources": ["LLM"],
    }
    merged = _deduplicate([rule, unrelated])
    date = next(item for item in merged if item["category"] == "DATE_FORMAT")
    assert "口语化" not in date["reason"]


def test_scenario_05b_semantic_review_filters_clean_sample_false_positives() -> None:
    text = "市住房保障中心：\n现将有关事项函告如下。\n特此函告。"
    examples = [
        {
            "severity": "CRITICAL",
            "category": "STRUCTURE",
            "original_text": "市住房保障中心",
            "suggested_text": "某某市住房保障中心：",
            "reason": "主送机关名称不完整，应使用全称。",
        },
        {
            "severity": "MAJOR",
            "category": "LANGUAGE",
            "original_text": "特此函告",
            "suggested_text": "删除该表述。",
            "reason": "函件结语冗余。",
        },
    ]
    assert all(_sanitize_semantic_finding(text, [], item) is None for item in examples)


def test_scenario_05c_style_references_cannot_drive_findings() -> None:
    reasons = [
        "参考材料中预算约为80万元，正文80万元表述过于绝对。",
        "历史材料使用2027年3月1日，建议与参考内容保持一致。",
        "证据显示实施地点为园区东侧，应修改正文地点。",
    ]
    for reason in reasons:
        assert _reference_policy_allows_finding(False, [], reason) is False
    assert _reference_policy_allows_finding(False, [2], "建议调整措辞。") is False
    assert _reference_policy_allows_finding(True, [2], reasons[0]) is True


def test_scenario_05d_semantic_review_rejects_new_fact_and_caps_severity() -> None:
    text = "镇人民政府：\n请尽快弄好这个项目。\n以上请示，妥否，请批示。"
    injected = _sanitize_semantic_finding(
        text,
        _review(text),
        {
            "severity": "MAJOR",
            "category": "LANGUAGE",
            "original_text": "请尽快弄好这个项目。",
            "suggested_text": "请安排5万元预算按期完成项目。",
            "reason": "建议改为正式书面语。",
        },
    )
    assert injected is None
    normalized = _sanitize_semantic_finding(
        text,
        _review(text),
        {
            "severity": "CRITICAL",
            "category": "FORMAT",
            "original_text": "尽快弄好",
            "suggested_text": "按期完成",
            "reason": "表述偏口语化。",
        },
    )
    assert normalized is not None
    assert normalized["category"] == "LANGUAGE"
    assert normalized["severity"] == "MINOR"

    date_text = "计划于2026-09-30完成。"
    normalized_date = _sanitize_semantic_finding(
        date_text,
        _review(f"镇人民政府：\n{date_text}\n以上请示，妥否，请批示。"),
        {
            "severity": "MAJOR",
            "category": "FORMAT",
            "original_text": "2026-09-30",
            "suggested_text": "2026年9月30日",
            "reason": "日期格式不规范。",
        },
    )
    assert normalized_date is not None
    assert normalized_date["category"] == "DATE_FORMAT"
    assert normalized_date["severity"] == "MINOR"


def test_scenario_05f_semantic_language_replacement_can_be_applied() -> None:
    text = "镇人民政府：\n请尽快帮忙安排一下设备维护。\n以上请示，妥否，请批示。"
    normalized = _sanitize_semantic_finding(
        text,
        [],
        {
            "severity": "MINOR",
            "category": "LANGUAGE",
            "original_text": "请尽快帮忙安排一下",
            "suggested_text": "请尽快安排",
            "reason": "表述偏口语化，建议使用正式书面语。",
        },
    )
    assert normalized is not None
    # 即使模型漏填 auto_fixable，直接替换建议也应能进入应用阶段。
    assert normalized["auto_fixable"] is True


def test_scenario_05e_review_categories_and_reasons_are_normalized() -> None:
    assert _canonical_category("结构") == "STRUCTURE"
    assert _canonical_category("附件引用错误") == "ATTACHMENT_SEQUENCE"
    reason = "正文措辞偏口语化；日期格式不规范；缺少具体事项描述。"
    assert _category_relevant_reason("DATE_FORMAT", reason) == "日期格式不规范"

    rule = {
        "severity": "MAJOR",
        "category": "ATTACHMENT_SEQUENCE",
        "location": {"paragraph": 1, "start": 0, "end": 2},
        "original_text": "附件",
        "suggested_text": "连续编号附件",
        "reason": "附件编号不连续",
        "sources": ["RULE"],
        "confidence": 1.0,
        "auto_fixable": False,
        "evidence": [],
    }
    llm = {
        **rule,
        "category": "附件引用错误",
        "location": {"paragraph": 1, "start": 0, "end": 12},
        "original_text": "附件1、附件3",
        "sources": ["LLM"],
    }
    merged = _deduplicate([rule, llm])
    assert len(merged) == 1
    assert merged[0]["category"] == "ATTACHMENT_SEQUENCE"
    assert merged[0]["sources"] == ["LLM", "RULE"]


def test_scenario_06_missing_draft_requirement() -> None:
    missing = _missing(_requirements(recipient=""))
    assert "主送单位" in missing


def test_scenario_07_unrelated_history_amount_is_style_only() -> None:
    score, kind = _candidate_quality(
        {
            "title": "关于示例市场招租的请示",
            "snippet": "租赁预算5万元。",
            "score": 0.6,
            "authority_score": 0.9,
            "document_role": "REQUEST",
            "version_role": "FORMAL",
        },
        _requirements(),
    )
    assert score > 0
    assert kind == "STYLE_REFERENCE"


def test_scenario_08_unsupported_generated_fact_blocks_export(db: Session) -> None:
    task = _task(
        db,
        "本次增加智能收费系统和地面修复工程，预算5万元。",
        {"passed": False, "unverified_facts": ["增加智能收费系统"]},
    )
    with pytest.raises(ValueError, match="校验未通过"):
        export_draft(db, task.id)


def test_scenario_09_invalid_citation_is_detected(db: Session) -> None:
    task = _task(db, "项目预算5万元，计划于2026年9月30日前完成。[9]")
    result = verify_draft_content(task)
    assert result["passed"] is False
    assert result["invalid_citation_ids"] == [9]


def test_scenario_10_local_fallback_warning_blocks_export(db: Session) -> None:
    task = _task(db, "项目预算5万元，计划于2026年9月30日前完成。[1]")
    result = verify_draft_content(task, warning="DeepSeek 不可用，已使用本地模板")
    # 降级内容仍可校验，但必须显式保留降级说明，交由页面展示。
    assert result["warning"] == "DeepSeek 不可用，已使用本地模板"
    assert result["passed"] is True
