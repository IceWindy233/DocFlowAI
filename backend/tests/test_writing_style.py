from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from docflow.db.models import DraftTask
from docflow.domain.agents import DraftRequirements
from docflow.domain.config import (
    GenreStyleBaseline,
    StyleMetric,
    StyleRange,
    WritingStyleConfig,
    default_runtime_config,
)
from docflow.services.config_service import ensure_default_config
from docflow.services.draft_agent import (
    _stance_position,
    export_draft,
    verify_draft_content,
)
from docflow.services.style_metrics import (
    compare_to_baseline,
    measure_style,
    quantiles,
    style_report,
)


def _requirements(**overrides) -> DraftRequirements:
    values = {
        "document_type": "REQUEST",
        "subject": "审议租赁补充合同",
        "recipient": "镇党委",
        "background": "二期物业已建成交付。",
        "facts": "年租金296万元，租期5年。",
        "requested_action": "请审议并批准签订。",
        "sender": "某集团有限公司",
        "date": "2026年9月4日",
    }
    values.update(overrides)
    return DraftRequirements.model_validate(values)


def _task(db: Session, text: str, **requirement_overrides) -> DraftTask:
    version = ensure_default_config(db)
    requirements = _requirements(**requirement_overrides)
    task = DraftTask(
        document_type=requirements.document_type,
        title=requirements.subject,
        requirements=requirements.model_dump(),
        config_version_id=version.id,
        draft_text=text,
        evidence_bundle=[{"id": 1}],
        verification={},
    )
    db.add(task)
    db.commit()
    return task


def test_mandatory_words_ignore_quoted_concepts_and_bufude() -> None:
    quoted = measure_style('分为“必须改”“可以缓”两类，不得不推迟的事项另行报批。')

    assert quoted.metrics[StyleMetric.MANDATORY_WORDS] == 0


def test_mandatory_words_count_real_constraints() -> None:
    plain = measure_style("承租方必须按期缴纳租金，不得转租，应当每季报送台账。")

    assert plain.metrics[StyleMetric.MANDATORY_WORDS] == 3


def test_placeholders_are_classified_and_gated() -> None:
    measurement = measure_style(
        "年租金【待补：xx】万元，参考量级【示意·待核】，依据表述【待核对原文】，"
        "另见【附件一】。"
    )

    assert measurement.placeholders["PENDING_VALUE"] == ["【待补：xx】"]
    assert measurement.placeholders["ILLUSTRATIVE"] == ["【示意·待核】"]
    assert measurement.placeholders["PENDING_SOURCE"] == ["【待核对原文】"]
    # 待核对原文只提示来源存疑，不阻断定稿。
    assert measurement.blocking_placeholders == ["【待补：xx】", "【示意·待核】"]
    assert measurement.unknown_placeholders == ["【附件一】"]


def test_percent_inside_placeholder_is_not_counted_as_data() -> None:
    measurement = measure_style("递增比例【待补：xx%】，实际执行5%。")

    assert measurement.metrics[StyleMetric.PERCENT_VALUES] == 1


def test_legal_colon_positions_are_not_reported() -> None:
    measurement = measure_style(
        "镇党委：\n来函收悉。现函复如下：\n一、同意该事项。\n经办人说：“已按期办理。”"
    )

    assert measurement.non_quote_colons == []


def test_colon_used_for_reveal_is_reported() -> None:
    measurement = measure_style("镇党委：\n拟采取的办法是：调整租期并追加保证金。")

    assert len(measurement.non_quote_colons) == 1


def test_heading_levels_are_counted_per_depth() -> None:
    measurement = measure_style(
        "一、工作目标\n（一）总体要求\n1. 摸清底数\n2. 建好台账\n二、重点任务\n"
    )

    assert measurement.metrics[StyleMetric.HEADING_LEVEL1] == 2
    assert measurement.metrics[StyleMetric.HEADING_LEVEL2] == 1
    assert measurement.metrics[StyleMetric.HEADING_LEVEL3] == 2


def test_quantiles_interpolate_between_samples() -> None:
    style_range = quantiles([10.0, 20.0, 30.0, 40.0, 50.0])

    assert (style_range.p25, style_range.median, style_range.p75) == (20.0, 30.0, 40.0)


def test_deviation_direction_reports_only_out_of_range_metrics() -> None:
    baseline = GenreStyleBaseline(
        sample_size=100,
        metrics={
            StyleMetric.CHARS: StyleRange(p25=300, median=460, p75=610),
            StyleMetric.SENTENCE_LENGTH: StyleRange(p25=60, median=72, p75=99),
            StyleMetric.DASHES: StyleRange(p25=0, median=0, p75=0),
        },
    )
    measurement = measure_style("镇党委：\n" + "拟调整租期。" * 20)

    deviations = {
        item.metric: item.direction for item in compare_to_baseline(measurement, baseline)
    }

    assert deviations[StyleMetric.SENTENCE_LENGTH] == "LOW"
    assert StyleMetric.DASHES not in deviations


def test_style_report_without_baseline_still_returns_measurements() -> None:
    report = style_report("镇党委：\n拟调整租期——并追加保证金。", None)

    assert report["baseline_available"] is False
    assert report["deviations"] == []
    assert report["metrics"]["dashes"] == 1


def test_reply_stance_leading_is_detected() -> None:
    leading = _stance_position("关于X的复函\n\n某公司：\n来函收悉。经研究，同意你公司所提事项。")
    trailing = _stance_position(
        "关于X的复函\n\n某公司：\n来函收悉。"
        + "该事项涉及多个环节需要逐项核对。" * 8
        + "同意办理。"
    )

    assert leading["leading"] is True
    assert trailing["found"] is True
    assert trailing["leading"] is False


def test_verification_keeps_style_deviation_out_of_passed(db: Session) -> None:
    draft = (
        "某集团有限公司关于审议租赁补充合同的请示\n\n镇党委：\n"
        "年租金296万元，租期5年。\n\n妥否，请批示。\n某集团有限公司\n"
    )
    task = _task(db, draft)
    style = WritingStyleConfig(
        baselines={
            "REQUEST": GenreStyleBaseline(
                sample_size=450,
                metrics={StyleMetric.CHARS: StyleRange(p25=300, median=460, p75=610)},
            )
        }
    )

    result = verify_draft_content(task, style=style)

    assert result["style_report"]["deviations"][0]["direction"] == "LOW"
    assert result["passed"] is True


def test_verification_lists_pending_placeholders(db: Session) -> None:
    task = _task(
        db,
        "某集团有限公司关于审议租赁补充合同的请示\n\n镇党委：\n"
        "年租金296万元，租期5年，文号【待补：xx】。\n\n妥否，请批示。\n某集团有限公司\n",
    )

    result = verify_draft_content(task)

    assert result["pending_placeholders"] == ["【待补：xx】"]
    assert result["passed"] is True


def test_export_is_blocked_by_pending_placeholders(db: Session) -> None:
    task = _task(
        db,
        "某集团有限公司关于审议租赁补充合同的请示\n\n镇党委：\n"
        "年租金296万元，租期5年，文号【待补：xx】。\n\n妥否，请批示。\n某集团有限公司\n",
    )
    task.verification = verify_draft_content(task)
    db.commit()

    with pytest.raises(ValueError, match="未补齐的占位符"):
        export_draft(db, task.id)


def test_placeholder_gate_can_be_disabled_per_configuration(db: Session) -> None:
    task = _task(
        db,
        "某集团有限公司关于审议租赁补充合同的请示\n\n镇党委：\n"
        "年租金296万元，租期5年，文号【待补：xx】。\n\n妥否，请批示。\n某集团有限公司\n",
    )
    task.verification = verify_draft_content(task)
    version = ensure_default_config(db)
    content = dict(version.content)
    content["writing_style"] = {
        **content.get("writing_style", {}),
        "placeholder_export_gate": False,
    }
    version.content = content
    db.commit()

    assert export_draft(db, task.id).endswith(".docx")


def test_default_configuration_ships_no_baseline_but_usable_defaults() -> None:
    style = default_runtime_config().writing_style

    # 基线必须由本地语料生成，不随仓库分发。
    assert style.baselines == {}
    assert style.long_line_fallback == 180
    assert "其实是" in style.meta_comment_words
    assert "相关部门" in style.bad_phrases
