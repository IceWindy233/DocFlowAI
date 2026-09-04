from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from docflow.db.models import AgentEvaluationRun
from docflow.domain.agent_evaluation import AgentEvaluationRunRequest
from docflow.domain.config import default_runtime_config
from docflow.services.agent_evaluation import (
    _aggregate_metrics,
    _correct_abstention,
    _fact_supported,
    _pricing_configured,
    _review_score,
    load_fixed_sample_set,
    run_fixed_evaluation,
)
from docflow.services.config_service import ensure_default_config


def test_fixed_catalog_can_be_loaded() -> None:
    data = load_fixed_sample_set()
    assert data["set_id"] == "docflow-agent-public-v1"
    assert len(data["qa_samples"]) == 9
    assert len(data["review_samples"]) == 5
    assert len(data["draft_samples"]) == 5


def test_expansion_catalog_inherits_review_samples() -> None:
    path = (
        Path(__file__).parents[2]
        / "evaluation"
        / "fixed-agent-samples-expansion-v1.json"
    )
    if not path.exists():
        pytest.skip("私有语料绑定评测集不会随公开仓库发布")
    data = load_fixed_sample_set(path)
    assert data["set_id"] == "docflow-agent-expansion-v1"
    assert len(data["qa_samples"]) == 10
    assert len(data["review_samples"]) == 5
    assert len(data["draft_samples"]) == 5


def test_qa_aggregate_separates_fact_and_locator_coverage() -> None:
    metrics = _aggregate_metrics(
        "QA",
        "LOCAL_RETRIEVAL",
        [
            {
                "passed": True,
                "recall_at_5": True,
                "locator_recall_at_5": True,
                "locator_coverage": 1.0,
                "fact_coverage": 1.0,
            },
            {
                "passed": True,
                "recall_at_5": True,
                "locator_recall_at_5": True,
                "locator_coverage": 0.5,
                "fact_coverage": 1.0,
            },
        ],
    )
    assert metrics["recall_at_5"] == 1.0
    assert metrics["fact_coverage"] == 1.0
    assert metrics["locator_recall_at_5"] == 1.0
    assert metrics["locator_evidence_coverage"] == 0.75
    assert metrics["locator_full_coverage_rate"] == 0.5


def test_agent_evaluation_mode_must_match_capability() -> None:
    with pytest.raises(ValidationError, match="QA 不支持运行模式 LOCAL_RULES"):
        AgentEvaluationRunRequest(capability="QA", mode="LOCAL_RULES")


def test_semantic_abstention_is_scored_without_exact_alias() -> None:
    expected = {
        "answer_aliases": ["未提供", "无法确定"],
        "forbidden_patterns": [r"\d+个停车位", r"共\d+个"],
    }
    correct, forbidden = _correct_abstention(
        "无法确认。证据中未提及示例园区南侧停车场的具体停车位数量。",
        "GENERATED",
        expected,
    )
    assert correct is True
    assert forbidden is False
    incorrect, forbidden = _correct_abstention(
        "现有材料不足，但我推测共120个停车位。",
        "SAFE_REFUSAL",
        expected,
    )
    assert incorrect is False
    assert forbidden is True


def test_negative_review_control_rejects_even_minor_false_positive() -> None:
    sample = {
        "id": "NEGATIVE-CONTROL",
        "name": "无问题对照组",
        "expected": {"required_categories": [], "maximum_finding_count": 0},
    }
    result = _review_score(
        sample,
        [
            {
                "severity": "MINOR",
                "category": "LANGUAGE",
                "location": {"start": 0},
                "original_text": "预算费用为80万元",
                "reason": "建议增加约字。",
                "sources": ["LLM"],
            }
        ],
    )
    assert result["passed"] is False
    assert result["finding_count"] == 1


def test_fact_aliases_and_reordered_parentheses_are_equivalent() -> None:
    expected = {
        "fact_aliases": {
            "某市司法部门": ["司法部门"],
        }
    }
    assert _fact_supported("司法部门明确表示无意见", expected, "某市司法部门")
    assert _fact_supported(
        "城市更新中心（规划管理所）明确表示无意见",
        expected,
        "规划管理所（城市更新中心）",
    )


def test_full_evaluation_marks_incomplete_cloud_pricing() -> None:
    config = default_runtime_config().model_copy(deep=True)
    config.routing.text_embedding_primary = "bailian_embedding"
    config.routing.qa_generation_primary = "cloud_chat_llm"
    assert _pricing_configured(config, "FULL_QA") is False
    assert _pricing_configured(config, "LOCAL_RETRIEVAL") is True

    profiles = {profile.profile_id: profile for profile in config.models}
    profiles["cloud_chat_llm"].price_input_per_million = 1
    profiles["cloud_chat_llm"].price_output_per_million = 2
    assert _pricing_configured(config, "FULL_QA") is True


def test_local_review_evaluation_runs_without_cloud(db: Session) -> None:
    ensure_default_config(db)
    result = run_fixed_evaluation(
        db,
        AgentEvaluationRunRequest(capability="REVIEW", mode="LOCAL_RULES"),
    )
    assert result["status"] == "SUCCEEDED"
    assert result["metrics"]["sample_count"] == 5
    assert result["metrics"]["pass_rate"] == 1.0
    assert result["metrics"]["issue_recall"] == 1.0
    assert result["metrics"]["duplicate_count"] == 0
    assert result["cloud_usage"]["calls"] == 0
    assert db.get(AgentEvaluationRun, result["id"]) is not None


def test_requirement_gate_evaluation_runs_without_cloud(db: Session) -> None:
    ensure_default_config(db)
    result = run_fixed_evaluation(
        db,
        AgentEvaluationRunRequest(capability="DRAFT", mode="REQUIREMENT_GATE"),
    )
    assert result["status"] == "SUCCEEDED"
    assert result["metrics"]["sample_count"] == 5
    assert result["metrics"]["pass_rate"] == 1.0
    gate = next(item for item in result["results"] if item["sample_id"] == "DRAFT-GATE-001")
    assert gate["passed"] is True
    assert "主送单位" in gate["missing_fields"]
    assert result["cloud_usage"]["calls"] == 0
