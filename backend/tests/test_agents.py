from sqlalchemy.orm import Session

from docflow.db.models import (
    ConfigVersion,
    DocumentReview,
    DraftTask,
    IngestionJob,
    Publication,
    ReviewFinding,
    WorkflowRun,
)
from docflow.domain.agents import DraftRequirements
from docflow.domain.config import RuntimeConfigBundleV1
from docflow.services.config_service import ensure_default_config
from docflow.services.draft_agent import (
    _claim_supported_by_requirements,
    _drafting_brief,
    _enforce_draft_presentation,
    _extract_outline_headings,
    _generate_outline,
    _missing,
    _normalize_outline,
    _redact_reference_text,
    _refine_outline_for_presentation,
    _resolve_presentation_mode,
    _style_evidence_projection,
    create_draft,
    export_draft,
    generate_draft,
    verify_draft_content,
)
from docflow.services.model_gateway import StructuredGenerationResult
from docflow.services.review_agent import apply_review, deterministic_review


def test_deterministic_review_finds_sensitive_date_and_language() -> None:
    text = (
        "关于停车场升级改造的请示\n"
        "镇人民政府：\n"
        "请于2026-01-12联系负责人13800138000，把这个项目尽快弄好。\n"
        "以上请示，妥否，请批示。"
    )
    findings = deterministic_review(
        text,
        "关于停车场升级改造的请示",
        ["STRUCTURE", "FORMAT", "LANGUAGE", "SENSITIVE"],
    )
    categories = {item["category"] for item in findings}
    assert "DATE_FORMAT" in categories
    assert "SENSITIVE_INFO" in categories
    assert "LANGUAGE" in categories
    assert any(item["auto_fixable"] for item in findings)


def test_apply_review_repairs_legacy_language_finding(db: Session) -> None:
    version = ensure_default_config(db)
    review = DocumentReview(
        title="设备维护请示",
        input_text="镇人民政府：\n请尽快帮忙安排一下设备维护。",
        scope=["LANGUAGE"],
        config_version_id=version.id,
    )
    db.add(review)
    db.flush()
    finding = ReviewFinding(
        review_id=review.id,
        severity="MINOR",
        category="LANGUAGE",
        original_text="请尽快帮忙安排一下",
        suggested_text="请尽快安排",
        reason="表述偏口语化",
        auto_fixable=False,
        status="ACCEPTED",
    )
    db.add(finding)
    db.commit()

    result = apply_review(db, review.id, [finding.id])

    assert "请尽快帮忙安排一下" not in result["revised_text"]
    assert "请尽快安排设备维护" in result["revised_text"]


def test_draft_requirement_gate_and_verifier(db: Session) -> None:
    version = ensure_default_config(db)
    incomplete = DraftRequirements(document_type="REQUEST", subject="测试事项")
    assert "主送单位" in _missing(incomplete)

    task = DraftTask(
        document_type="REQUEST",
        title="停车场升级改造",
        requirements={
            "document_type": "REQUEST",
            "subject": "停车场升级改造",
            "recipient": "镇人民政府",
            "background": "根据会议要求，计划于2026年1月12日实施。",
            "facts": "预算费用为5万元。",
            "requested_action": "申请同意实施。",
            "sender": "某市示例产业运营有限公司",
            "date": "2026年1月12日",
            "reference_query": "停车场升级改造",
        },
        config_version_id=version.id,
        evidence_bundle=[{"id": 1}],
        draft_text="预算费用为5万元，计划于2026年1月12日实施。[1]",
    )
    assert verify_draft_content(task)["passed"] is True
    task.draft_text = "预算待定。[9]"
    result = verify_draft_content(task)
    assert result["passed"] is False
    assert "5万元" in result["missing_required_facts"]
    assert result["invalid_citation_ids"] == [9]
    task.draft_text = "预算费用为5万元。[证据1]"
    result = verify_draft_content(task)
    assert result["invalid_citation_ids"] == []
    assert result["citation_count"] == 1


def test_reference_text_redacts_contact_numbers() -> None:
    value = "联系人：张三，联系电话：13800138000\n联系人：李四，电话：83722807"
    redacted = _redact_reference_text(value)
    assert "13800138000" not in redacted
    assert "83722807" not in redacted


def test_style_reference_projection_does_not_expose_facts() -> None:
    projected = _style_evidence_projection(
        [
            {
                "id": 1,
                "title": "招租方案请示",
                "snippet": "一、基本情况\n预算80万元。\n二、实施安排",
                "evidence_type": "STYLE_REFERENCE",
            }
        ]
    )
    assert projected[0]["style_headings"] == ["一、基本情况", "二、实施安排"]
    assert "80万元" not in str(projected)
    assert projected[0]["facts_authorized"] is False


def test_outline_heading_extraction_uses_full_document_structure() -> None:
    text = (
        "关于项目借款的请示\n镇政府：\n一、基本情况\n项目具体事实。\n"
        "二、请示事项\n（一）申请办理借款。\n以上请示妥否，请批示。"
    )
    assert _extract_outline_headings(text) == ["基本情况", "请示事项"]


def test_outline_normalization_removes_numbering_duplicates_and_closing() -> None:
    value = [
        {"id": "a", "title": "一、基本情况"},
        {"id": "b", "title": "二、请示事项"},
        {"id": "c", "title": "请示事项"},
        {"id": "d", "title": "恳请批复"},
    ]
    assert _normalize_outline(value, "REQUEST") == [
        {"id": "a", "title": "基本情况"},
        {"id": "b", "title": "请示事项"},
    ]


def test_complex_multi_item_request_forces_sectioned_presentation() -> None:
    requirements = DraftRequirements(
        document_type="REQUEST",
        subject="产业园综合提升",
        recipient="镇政府",
        background="部分基础设施使用年限较长。",
        facts=("项目包括三个子项：路面改造、照明改造和充电车位建设；计划分两个阶段实施。"),
        requested_action="申请同意项目立项、资金安排及后续采购工作。",
        sender="测试单位",
    )
    assert _resolve_presentation_mode(requirements, "PARAGRAPH") == "SECTIONED"
    outline = _refine_outline_for_presentation(
        [
            {"id": "1", "title": "项目背景"},
            {"id": "2", "title": "建设内容与投资"},
            {"id": "3", "title": "实施计划"},
            {"id": "4", "title": "请示事项"},
        ],
        "SECTIONED",
        requirements,
    )
    assert [item["title"] for item in outline] == [
        "建设内容与投资",
        "实施计划",
        "请示事项",
    ]


def test_simple_single_matter_request_stays_paragraph_even_if_model_proposes_sections() -> None:
    requirements = DraftRequirements(
        document_type="REQUEST",
        subject="消防设施升级改造",
        recipient="镇政府",
        background="现有消防报警设备老化，部分区域无法实现集中监控。",
        facts="更换控制器、增设烟感设备并完善联动系统；预算48万元；工期60天。",
        requested_action="申请同意实施改造并按程序采购。",
        sender="测试单位",
    )
    assert _resolve_presentation_mode(requirements, "SECTIONED") == "PARAGRAPH"


def test_drafting_brief_uses_genre_specific_order_and_closing() -> None:
    request = DraftRequirements(
        document_type="REQUEST",
        subject="申请流动资金贷款",
        recipient="某银行",
        background="为保障园区运营周转。",
        facts="贷款500万元，期限12个月。",
        requested_action="申请同意办理流动资金贷款。",
        sender="测试单位",
    )
    brief = _drafting_brief(
        request,
        [{"id": "request", "title": "请示事项", "render_heading": False}],
    )
    assert brief["presentation_mode"] == "PARAGRAPH"
    assert brief["content_order"][-1] == "明确请示事项"
    assert brief["closing"] == "妥否，请批示。"
    assert len(brief["style_guide"]) >= 5
    assert any("力度词" in rule for rule in brief["style_guide"])
    assert any("文种" in rule for rule in brief["silent_self_check"])

    reply = request.model_copy(
        update={
            "document_type": "LETTER",
            "subject": "回复征求意见函",
            "background": "《征求意见函》收悉。",
            "requested_action": "现就有关意见予以函复。",
        }
    )
    reply_brief = _drafting_brief(reply, [])
    assert reply_brief["genre"] == "复函"
    assert reply_brief["content_order"][1] == "先明确答复结论"
    assert reply_brief["closing"] == "特此函复。"

    request_for_feedback = request.model_copy(
        update={
            "document_type": "LETTER",
            "subject": "征求项目方案意见",
            "requested_action": "请于五个工作日内反馈意见并予以复函。",
        }
    )
    feedback_brief = _drafting_brief(request_for_feedback, [])
    assert feedback_brief["genre"] == "函"
    assert feedback_brief["closing"] == "专此函达，请予复函。"


def test_draft_presentation_guard_removes_headings_and_unrequested_attachments() -> None:
    requirements = DraftRequirements(
        document_type="REQUEST",
        subject="设备采购",
        recipient="镇政府",
        background="现有设备老化。",
        facts="拟采购2台设备，预算8万元。",
        requested_action="申请同意采购。",
        sender="测试单位",
    )
    draft_text = (
        "关于设备采购的请示\n\n镇政府：\n\n现将有关情况请示如下：\n\n"
        "一、基本情况\n\n现有设备老化。\n\n二、请示事项\n\n"
        "申请同意采购2台设备。\n\n妥否，请批示。\n\n"
        "附件：1.采购方案\n2.预算明细表\n\n测试单位\n2026年8月18日"
    )
    guarded = _enforce_draft_presentation(
        draft_text,
        [
            {"id": "1", "title": "基本情况", "render_heading": False},
            {"id": "2", "title": "请示事项", "render_heading": False},
        ],
        requirements,
    )
    assert "一、基本情况" not in guarded
    assert "现将有关情况请示如下" not in guarded
    assert "附件：" not in guarded
    assert "测试单位" in guarded


def test_sectioned_draft_guard_deduplicates_intro_and_first_section() -> None:
    requirements = DraftRequirements(
        document_type="REQUEST",
        subject="园区综合提升",
        recipient="镇政府",
        background="园区部分基础设施使用年限较长。",
        facts="项目包括三个子项。",
        requested_action="申请同意实施。",
        sender="测试单位",
    )
    draft_text = (
        "关于园区综合提升的请示\n\n镇政府：\n\n"
        "园区部分基础设施使用年限较长。现将有关事项请示如下：\n\n"
        "一、项目背景\n\n园区部分基础设施使用年限较长。\n\n"
        "二、请示事项\n\n申请同意实施。\n\n妥否，请批示。\n\n测试单位"
    )
    guarded = _enforce_draft_presentation(
        draft_text,
        [
            {"id": "1", "title": "项目背景", "render_heading": True},
            {"id": "2", "title": "请示事项", "render_heading": True},
        ],
        requirements,
    )
    assert guarded.count("园区部分基础设施使用年限较长") == 1
    assert "现将有关事项请示如下：\n\n一、项目背景" in guarded


def test_outline_prompt_prefers_reference_structure(db: Session, monkeypatch) -> None:
    version = ensure_default_config(db)
    captured: dict = {}

    monkeypatch.setattr(
        "docflow.services.draft_agent._reference_outline_structures",
        lambda *_args: [
            {
                "title": "关于项目借款的请示",
                "top_level_headings": ["基本情况", "请示事项"],
                "facts_authorized": False,
            }
        ],
    )

    def fake_generation(*_args, system_prompt: str, payload: dict, **_kwargs):
        captured["system_prompt"] = system_prompt
        captured["payload"] = payload
        return StructuredGenerationResult(
            content={
                "presentation_mode": "PARAGRAPH",
                "outline": [
                    {"id": "background", "title": "基本情况"},
                    {"id": "request", "title": "请示事项"},
                ],
            },
            model_signature="fake:outline",
            usage={"calls": 1, "input_tokens": 10, "output_tokens": 5},
        )

    monkeypatch.setattr("docflow.services.draft_agent.generate_structured_content", fake_generation)
    requirements = DraftRequirements(
        document_type="REQUEST",
        subject="申请流动资金贷款",
        recipient="某商业银行示例分行",
        background="为保障园区日常运营和资金周转需要。",
        facts="贷款金额500万元，期限12个月。",
        requested_action="申请同意办理流动资金贷款。",
        sender="测试单位",
    )
    outline, *_ = _generate_outline(
        db, RuntimeConfigBundleV1.model_validate(version.content), requirements, []
    )
    assert outline == [
        {"id": "background", "title": "基本情况", "render_heading": False},
        {"id": "request", "title": "请示事项", "render_heading": False},
    ]
    assert captured["payload"]["reference_structures"][0]["top_level_headings"] == [
        "基本情况",
        "请示事项",
    ]
    assert captured["payload"]["presentation_constraints"]["required_mode"] == "MODEL_DECIDES"
    assert "恳请批复" in captured["system_prompt"]
    assert "绝对不得成为章节" in captured["system_prompt"]
    assert "presentation_mode" in captured["system_prompt"]


def test_requirement_supported_claim_is_not_unverified() -> None:
    requirements = DraftRequirements(
        document_type="REQUEST",
        subject="场地招租",
        recipient="镇人民政府",
        background="拟开展场地招租。",
        facts="合同面积24977平方米，租赁期限5年。",
        requested_action="申请审议。",
        sender="测试单位",
    )
    assert _claim_supported_by_requirements("合同面积24977平方米", requirements)
    assert not _claim_supported_by_requirements("免租期1个月", requirements)


def test_incomplete_draft_uses_real_langgraph_gate(db: Session) -> None:
    ensure_default_config(db)
    result = create_draft(db, DraftRequirements(document_type="REQUEST", subject="测试事项"))
    run = db.get(WorkflowRun, result["workflow_run_id"])
    assert result["status"] == "NEEDS_REQUIREMENTS"
    assert run is not None
    assert run.engine == "langgraph-stategraph"
    assert [item["node"] for item in run.trace_json] == ["requirement_validator"]


def test_unverified_draft_cannot_export(db: Session) -> None:
    version = ensure_default_config(db)
    task = DraftTask(
        document_type="REQUEST",
        title="测试事项",
        requirements={
            "document_type": "REQUEST",
            "subject": "测试事项",
            "recipient": "镇人民政府",
            "background": "测试背景",
            "facts": "预算5万元",
            "requested_action": "申请同意实施",
            "sender": "测试单位",
            "date": "2026年8月13日",
            "reference_query": "",
        },
        config_version_id=version.id,
        draft_text="测试初稿正文超过二十个字符，但其中仍然存在没有核实的事实内容。",
        verification={"passed": False},
    )
    db.add(task)
    db.commit()
    try:
        export_draft(db, task.id)
        raise AssertionError("未通过校验的初稿不应允许导出")
    except ValueError as exc:
        assert "校验未通过" in str(exc)


def test_failed_generated_draft_is_repaired_once(db: Session, monkeypatch) -> None:
    version = ensure_default_config(db)
    task = DraftTask(
        document_type="REQUEST",
        title="停车场升级改造",
        status="OUTLINE_APPROVED",
        requirements={
            "document_type": "REQUEST",
            "subject": "停车场升级改造",
            "recipient": "镇人民政府",
            "background": "根据会议要求实施升级改造。",
            "facts": "项目预算5万元，计划于2026年9月30日前完成。",
            "requested_action": "申请同意实施。",
            "sender": "测试单位",
            "date": "2026年8月13日",
            "reference_query": "",
        },
        outline=[{"id": "facts", "title": "有关情况"}],
        evidence_bundle=[],
        config_version_id=version.id,
        cloud_usage={},
    )
    db.add(task)
    db.commit()

    def fake_generation(*_args, purpose: str, **_kwargs):
        usage = {"calls": 1, "input_tokens": 10, "output_tokens": 10}
        if purpose == "公文初稿生成":
            return StructuredGenerationResult(
                content={
                    "draft_text": (
                        "关于停车场升级改造的请示\n镇人民政府：\n项目预算5万元，计划于"
                        "2026年9月30日前完成，并新增无人机设备10台。\n以上请示，妥否，请批示。"
                    ),
                    "unverified_facts": ["主送单位待补充"],
                },
                model_signature="fake:draft",
                usage=usage,
            )
        if purpose == "公文事实核验":
            draft_text = _kwargs["payload"]["draft_text"]
            unsupported = ["无人机设备10台"] if "无人机" in draft_text else []
            return StructuredGenerationResult(
                content={"unsupported_claims": unsupported},
                model_signature="fake:verify",
                usage=usage,
            )
        assert purpose == "公文初稿约束修复"
        return StructuredGenerationResult(
            content={
                "draft_text": (
                    "关于停车场升级改造的请示\n镇人民政府：\n根据会议要求实施升级改造。"
                    "项目预算5万元，计划于2026年9月30日前完成。申请同意实施。\n"
                    "以上请示，妥否，请批示。\n测试单位\n2026年8月13日"
                ),
                "unverified_facts": [],
            },
            model_signature="fake:repair",
            usage=usage,
        )

    monkeypatch.setattr("docflow.services.draft_agent.generate_structured_content", fake_generation)
    result = generate_draft(db, task.id)
    assert result["verification"]["passed"] is True
    assert result["verification"]["repair_attempted"] is True
    assert "无人机" not in result["draft_text"]
    run = db.get(WorkflowRun, result["workflow_run_id"])
    assert run is not None
    assert [item["node"] for item in run.trace_json] == [
        "draft_composer",
        "fact_verifier",
        "draft_repairer",
        "repair_verifier",
    ]


def test_agent_tables_are_available(db: Session) -> None:
    version = ensure_default_config(db)
    job = IngestionJob(
        job_type="FULL_SCAN",
        source_root="/tmp",
        config_version_id=version.id,
        index_generation_id="idx_agent",
    )
    db.add(job)
    db.add(
        Publication(
            config_version_id=version.id,
            index_generation_id="idx_agent",
            status="PUBLISHED",
            active=True,
        )
    )
    db.commit()
    assert db.get(ConfigVersion, version.id) is not None
