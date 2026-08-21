from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from docflow.core.settings import PROJECT_ROOT
from docflow.db.models import (
    AgentEvaluationRun,
    Document,
    DraftTask,
    IngestionJob,
    Page,
    Publication,
    SourceFile,
    utcnow,
)
from docflow.domain.agent_evaluation import AgentEvaluationRunRequest
from docflow.domain.agents import DocumentReviewCreate, DraftRequirements
from docflow.domain.config import AdapterType, ModelCapability, RuntimeConfigBundleV1
from docflow.domain.retrieval import RetrievalAnswerRequest
from docflow.services.config_service import get_current_config
from docflow.services.draft_agent import (
    _missing,
    create_draft,
    generate_draft,
    update_outline,
    verify_draft_content,
)
from docflow.services.retrieval import (
    RetrievalContextError,
    _resolve_context,
    answer,
    search_text_pages,
)
from docflow.services.review_agent import create_review, deterministic_review

SAMPLE_DIR = PROJECT_ROOT / "evaluation"
SAMPLE_PATH = SAMPLE_DIR / "fixed-agent-samples-public-v1.json"
REFUSAL_PATTERNS = re.compile(
    r"(?:无法(?:确认|确定|判断|得知)|未(?:提供|提及|说明|找到)|没有(?:提供|提及|说明)|"
    r"证据不足|材料不足|无相关信息)"
)


def load_fixed_sample_set(
    path: Path = SAMPLE_PATH,
    _seen: frozenset[Path] = frozenset(),
) -> dict[str, Any]:
    path = path.resolve()
    if path in _seen:
        raise ValueError(f"评测集继承存在循环：{path.name}")
    if not path.exists():
        raise LookupError(f"固定评测集不存在：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if parent_name := data.get("inherits"):
        parent_path = (SAMPLE_DIR / str(parent_name)).resolve()
        if parent_path.parent != SAMPLE_DIR.resolve():
            raise ValueError("评测集 inherits 只能引用 evaluation 目录内的文件")
        parent = load_fixed_sample_set(parent_path, _seen | {path})
        data = {
            **parent,
            **data,
            "review_samples": data.get("review_samples", parent["review_samples"]),
            "draft_samples": data.get("draft_samples", parent["draft_samples"]),
            "qa_samples": data.get("qa_samples", parent["qa_samples"]),
        }
    if data.get("schema_version") != "1.0":
        raise ValueError("不支持的固定评测集版本")
    return data


def load_context_sample_set(db: Session) -> dict[str, Any]:
    """优先加载与当前可检索索引代际精确匹配的固定评测集。"""
    try:
        context = _resolve_context(db)
    except RetrievalContextError:
        # 纯规则审核和需求门禁不依赖知识库，空库也应能运行。
        return load_fixed_sample_set()
    for path in sorted(SAMPLE_DIR.glob("fixed-agent-samples*.json")):
        data = load_fixed_sample_set(path)
        if (
            data.get("corpus_binding", {}).get("index_generation_id")
            == context.index_generation_id
        ):
            return data
    return load_fixed_sample_set()


def _usage_add(total: dict[str, Any], item: dict[str, Any]) -> None:
    for key in ["calls", "input_tokens", "output_tokens"]:
        total[key] = int(total.get(key, 0)) + int(item.get(key, 0) or 0)
    total["estimated_cost_cny"] = round(
        float(total.get("estimated_cost_cny", 0))
        + float(item.get("estimated_cost_cny", 0) or 0),
        6,
    )


def _pricing_configured(config: RuntimeConfigBundleV1, mode: str) -> bool:
    """Return whether every cloud model used by a full evaluation has usable pricing."""
    if not mode.startswith("FULL_"):
        return True
    profiles = {profile.profile_id: profile for profile in config.models}
    route_ids = (
        config.routing.text_embedding_primary,
        config.routing.reranker_primary,
        config.routing.qa_generation_primary,
    )
    cloud_adapters = {AdapterType.DASHSCOPE_OPENAI, AdapterType.DEEPSEEK_OPENAI}
    routed_cloud_profiles = [
        profiles[profile_id]
        for profile_id in route_ids
        if profile_id
        and profile_id in profiles
        and profiles[profile_id].adapter_type in cloud_adapters
    ]
    if not routed_cloud_profiles:
        return False
    return all(
        profile.price_input_per_million > 0
        and (
            profile.capability != ModelCapability.CHAT_LLM
            or profile.price_output_per_million > 0
        )
        for profile in routed_cloud_profiles
    )


def _run_dict(run: AgentEvaluationRun, include_results: bool = True) -> dict[str, Any]:
    return {
        "id": run.id,
        "sample_set_id": run.sample_set_id,
        "capability": run.capability,
        "mode": run.mode,
        "status": run.status,
        "config_version_id": run.config_version_id,
        "index_generation_id": run.index_generation_id,
        "metrics": run.metrics or {},
        "results": run.results or [] if include_results else [],
        "cloud_usage": run.cloud_usage or {},
        "error_message": run.error_message,
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def _active_publication(db: Session) -> Publication | None:
    return db.scalar(
        select(Publication)
        .where(Publication.active.is_(True), Publication.status == "PUBLISHED")
        .order_by(Publication.published_at.desc())
    )


def _resolve_evidence(
    db: Session,
    evidence: dict[str, Any],
    index_generation_id: str,
    config_version_id: str,
) -> dict[str, Any]:
    row = db.execute(
        select(Document, SourceFile)
        .join(SourceFile, Document.source_file_id == SourceFile.id)
        .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
        .where(
            SourceFile.sha256 == evidence["source_sha256"],
            IngestionJob.index_generation_id == index_generation_id,
            Document.config_version_id == config_version_id,
        )
        .limit(1)
    ).first()
    if not row:
        return {**evidence, "resolved": False, "document_id": None, "page_id": None}
    document, source = row
    page = db.scalar(
        select(Page).where(
            Page.document_id == document.id,
            Page.page_number == int(evidence["page_number"]),
        )
    )
    return {
        **evidence,
        "resolved": page is not None,
        "document_id": document.id,
        "page_id": page.id if page else None,
        "resolved_title": document.title,
        "resolved_file_name": source.file_name,
    }


def _resolved_qa_samples(db: Session, data: dict[str, Any]) -> list[dict[str, Any]]:
    context = _resolve_context(db)
    values = []
    for sample in data["qa_samples"]:
        evidence = [
            _resolve_evidence(
                db,
                item,
                context.index_generation_id,
                context.config_version_id,
            )
            for item in sample["expected"]["evidence"]
        ]
        values.append(
            {
                **sample,
                "expected": {**sample["expected"], "evidence": evidence},
                "resolvable": all(item["resolved"] for item in evidence),
            }
        )
    return values


def fixed_catalog(db: Session) -> dict[str, Any]:
    data = load_context_sample_set(db)
    context = _resolve_context(db)
    qa_samples = _resolved_qa_samples(db, data)
    return {
        "set_id": data["set_id"],
        "name": data["name"],
        "description": data["description"],
        "schema_version": data["schema_version"],
        "distribution": data["selection_policy"]["distribution"],
        "context": {
            "config_version_id": context.config_version_id,
            "index_generation_id": context.index_generation_id,
            "source": context.source,
            "source_publication_matches_snapshot": (
                context.index_generation_id
                == data["corpus_binding"].get("index_generation_id")
            ),
        },
        "qa_samples": qa_samples,
        "review_samples": data["review_samples"],
        "draft_samples": data["draft_samples"],
        "resolution": {
            "qa_sample_count": len(qa_samples),
            "resolvable_count": sum(item["resolvable"] for item in qa_samples),
            "evidence_count": sum(
                len(item["expected"]["evidence"]) for item in qa_samples
            ),
            "resolved_evidence_count": sum(
                evidence["resolved"]
                for item in qa_samples
                for evidence in item["expected"]["evidence"]
            ),
        },
    }


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]", "", value.lower()).replace("的", "")


def _fact_present(text: str, fact: str) -> bool:
    answer_value, fact_value = _normalized(text), _normalized(fact)
    if fact_value in answer_value:
        return True
    components = [
        _normalized(value)
        for value in re.split(r"[（()）/、]", fact)
        if len(_normalized(value)) >= 2
    ]
    if len(components) >= 2 and all(component in answer_value for component in components):
        return True
    tokens = set(re.findall(r"\d+(?:\.\d+)?|[\u3400-\u9fff]{2,}", fact_value))
    return bool(tokens) and all(token in answer_value for token in tokens)


def _fact_options(expected: dict[str, Any], fact: str) -> list[str]:
    aliases = (expected.get("fact_aliases") or {}).get(fact) or []
    return [fact, *[str(value) for value in aliases]]


def _fact_supported(text: str, expected: dict[str, Any], fact: str) -> bool:
    return any(_fact_present(text, option) for option in _fact_options(expected, fact))


def _page_texts(db: Session, page_ids: set[str]) -> list[str]:
    if not page_ids:
        return []
    return [
        str(value or "")
        for value in db.scalars(select(Page.text).where(Page.id.in_(page_ids)))
    ]


def _correct_abstention(
    answer_text: str,
    answer_mode: str | None,
    expected: dict[str, Any],
) -> tuple[bool, bool]:
    forbidden = any(
        re.search(pattern, answer_text) for pattern in expected.get("forbidden_patterns") or []
    )
    aliases = expected.get("answer_aliases") or []
    alias_hit = any(_normalized(alias) in _normalized(answer_text) for alias in aliases)
    refusal_hit = answer_mode == "SAFE_REFUSAL" or bool(REFUSAL_PATTERNS.search(answer_text))
    return (alias_hit or refusal_hit) and not forbidden, forbidden


def _selected(samples: list[dict[str, Any]], sample_ids: list[str]) -> list[dict[str, Any]]:
    if not sample_ids:
        return samples
    wanted = set(sample_ids)
    values = [sample for sample in samples if sample["id"] in wanted]
    missing = wanted - {sample["id"] for sample in values}
    if missing:
        raise LookupError(f"评测样例不存在：{', '.join(sorted(missing))}")
    return values


def _qa_result(
    db: Session,
    sample: dict[str, Any],
    mode: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected = sample["expected"]
    expected_pages = {
        item["page_id"] for item in expected["evidence"] if item.get("page_id")
    }
    if mode == "LOCAL_RETRIEVAL":
        context = _resolve_context(db)
        hits = search_text_pages(db, context, sample["question"], 10)
        top_five = [item.page_id for item in hits[:5]]
        matched = expected_pages & set(top_five)
        answerable = expected["behavior"] == "ANSWER"
        facts = expected.get("required_facts") or []
        top_texts = _page_texts(db, set(top_five))
        supported = [
            fact
            for fact in facts
            if any(_fact_supported(text, expected, fact) for text in top_texts)
        ]
        fact_recall = len(supported) / len(facts) if facts else 1.0
        return (
            {
                "sample_id": sample["id"],
                "name": sample["name"],
                "question": sample["question"],
                "passed": fact_recall == 1.0 if answerable else None,
                "behavior": expected["behavior"],
                "recall_at_5": fact_recall == 1.0 if answerable else None,
                "evidence_coverage": round(fact_recall, 4) if answerable else None,
                "locator_recall_at_5": bool(matched) if answerable else None,
                "locator_coverage": round(len(matched) / len(expected_pages), 4)
                if expected_pages
                else None,
                "expected_page_ids": sorted(expected_pages),
                "retrieved_page_ids": top_five,
            },
            {},
        )

    response = answer(
        db,
        RetrievalAnswerRequest(
            query=sample["question"],
            mode="hybrid",
            limit=10,
            evidence_limit=5,
            debug=True,
        ),
    )
    generated = str(response.get("answer") or "")
    citation_pages = {item["page_id"] for item in response.get("citations") or []}
    top_five = {
        item["page_id"] for item in response.get("retrieval", {}).get("results", [])[:5]
    }
    if expected["behavior"] == "ABSTAIN":
        passed, forbidden = _correct_abstention(
            generated,
            str(response.get("answer_mode") or ""),
            expected,
        )
        return (
            {
                "sample_id": sample["id"],
                "name": sample["name"],
                "question": sample["question"],
                "passed": passed,
                "behavior": "ABSTAIN",
                "answer": generated,
                "answer_mode": response.get("answer_mode"),
                "abstention_correct": passed,
                "forbidden_claim_detected": forbidden,
                "citation_page_ids": sorted(citation_pages),
            },
            response.get("cloud_usage") or {},
        )
    facts = expected.get("required_facts") or []
    fact_hits = [fact for fact in facts if _fact_supported(generated, expected, fact)]
    matched_citations = expected_pages & citation_pages
    matched_retrieval = expected_pages & top_five
    citation_texts = _page_texts(db, citation_pages)
    retrieval_texts = _page_texts(db, top_five)
    citation_supported = [
        fact
        for fact in facts
        if any(_fact_supported(text, expected, fact) for text in citation_texts)
    ]
    retrieval_supported = [
        fact
        for fact in facts
        if any(_fact_supported(text, expected, fact) for text in retrieval_texts)
    ]
    fact_coverage = len(fact_hits) / len(facts) if facts else 1.0
    citation_coverage = len(citation_supported) / len(facts) if facts else 1.0
    retrieval_coverage = len(retrieval_supported) / len(facts) if facts else 1.0
    passed = fact_coverage == 1.0 and citation_coverage == 1.0
    return (
        {
            "sample_id": sample["id"],
            "name": sample["name"],
            "question": sample["question"],
            "passed": passed,
            "behavior": "ANSWER",
            "answer": generated,
            "answer_mode": response.get("answer_mode"),
            "fact_coverage": round(fact_coverage, 4),
            "matched_facts": fact_hits,
            "missing_facts": [fact for fact in facts if fact not in fact_hits],
            "recall_at_5": retrieval_coverage == 1.0,
            "evidence_coverage": round(retrieval_coverage, 4),
            "citation_correct": citation_coverage == 1.0,
            "citation_coverage": round(citation_coverage, 4),
            "citation_supported_facts": citation_supported,
            "locator_recall_at_5": bool(matched_retrieval),
            "locator_evidence_coverage": round(
                len(matched_retrieval) / len(expected_pages), 4
            )
            if expected_pages
            else None,
            "locator_citation_coverage": round(
                len(matched_citations) / len(expected_pages), 4
            )
            if expected_pages
            else None,
            "expected_page_ids": sorted(expected_pages),
            "citation_page_ids": sorted(citation_pages),
        },
        response.get("cloud_usage") or {},
    )


def _review_score(sample: dict[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
    expected = sample["expected"]
    categories = [str(item.get("category") or "") for item in findings]
    required = expected.get("required_categories") or []
    matched = [category for category in required if category in categories]
    checks = [len(matched) == len(required)]
    for category, minimum in expected.get("minimum_category_counts", {}).items():
        checks.append(categories.count(category) >= int(minimum))
    if "minimum_finding_count" in expected:
        checks.append(len(findings) >= int(expected["minimum_finding_count"]))
    if "maximum_finding_count" in expected:
        checks.append(len(findings) <= int(expected["maximum_finding_count"]))
    if "maximum_rule_finding_count" in expected:
        rule_count = sum("RULE" in (item.get("sources") or ["RULE"]) for item in findings)
        checks.append(rule_count <= int(expected["maximum_rule_finding_count"]))
    severe_count = sum(
        str(item.get("severity")) in {"CRITICAL", "MAJOR"} for item in findings
    )
    if "maximum_major_or_critical_count" in expected:
        checks.append(severe_count <= int(expected["maximum_major_or_critical_count"]))
    identities = {
        (
            item.get("category"),
            (item.get("location") or {}).get("start"),
            _normalized(str(item.get("original_text") or "")),
            (
                ""
                if item.get("original_text")
                else _normalized(str(item.get("reason") or ""))
            ),
        )
        for item in findings
    }
    return {
        "sample_id": sample["id"],
        "name": sample["name"],
        "passed": all(checks),
        "required_categories": required,
        "matched_categories": matched,
        "category_recall": round(len(matched) / len(required), 4) if required else 1.0,
        "finding_count": len(findings),
        "severe_count": severe_count,
        "duplicate_count": max(0, len(findings) - len(identities)),
        "findings": [
            {
                key: item.get(key)
                for key in [
                    "severity",
                    "category",
                    "original_text",
                    "suggested_text",
                    "reason",
                    "sources",
                ]
            }
            for item in findings
        ],
    }


def _review_result(
    db: Session, sample: dict[str, Any], mode: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    if mode == "LOCAL_RULES":
        findings = deterministic_review(
            sample["text"], sample["title"], sample["scope"]
        )
        return _review_score(sample, findings), {}
    review = create_review(
        db,
        DocumentReviewCreate(
            title=sample["title"],
            text=sample["text"],
            scope=sample["scope"],
        ),
    )
    result = _review_score(sample, review.get("findings") or [])
    result["review_id"] = review["id"]
    result["workflow_run_id"] = review.get("workflow_run_id")
    return result, review.get("cloud_usage") or {}


def _preferred_document_ids(
    db: Session, hashes: list[str], index_generation_id: str, config_version_id: str
) -> set[str]:
    if not hashes:
        return set()
    return set(
        db.scalars(
            select(Document.id)
            .join(SourceFile, Document.source_file_id == SourceFile.id)
            .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
            .where(
                SourceFile.sha256.in_(hashes),
                IngestionJob.index_generation_id == index_generation_id,
                Document.config_version_id == config_version_id,
            )
        )
    )


def _draft_gate_result(sample: dict[str, Any]) -> dict[str, Any]:
    requirements = DraftRequirements.model_validate(sample["requirements"])
    missing = _missing(requirements)
    expected = sample["expected"]
    expected_missing = expected.get("required_missing_fields") or []
    passed = (
        not missing
        if expected["should_create"]
        else all(item in missing for item in expected_missing)
    )
    return {
        "sample_id": sample["id"],
        "name": sample["name"],
        "passed": passed,
        "should_create": expected["should_create"],
        "missing_fields": missing,
        "expected_missing_fields": expected_missing,
    }


def _draft_result(
    db: Session,
    sample: dict[str, Any],
    mode: str,
    index_generation_id: str,
    config_version_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if mode == "REQUIREMENT_GATE":
        return _draft_gate_result(sample), {}
    expected = sample["expected"]
    planned = create_draft(db, DraftRequirements.model_validate(sample["requirements"]))
    if not expected["should_create"]:
        result = _draft_gate_result(sample)
        result["draft_id"] = planned["id"]
        result["actual_status"] = planned["status"]
        result["passed"] = result["passed"] and planned["status"] == expected["expected_status"]
        return result, planned.get("cloud_usage") or {}
    update_outline(db, planned["id"], planned["outline"])
    generated = generate_draft(db, planned["id"])
    draft_text = str(generated.get("draft_text") or "")
    facts = expected.get("required_facts_in_draft") or []
    matched_facts = [fact for fact in facts if _fact_present(draft_text, fact)]
    fact_coverage = len(matched_facts) / len(facts) if facts else 1.0
    preferred_ids = _preferred_document_ids(
        db,
        expected.get("preferred_evidence_sha256") or [],
        index_generation_id,
        config_version_id,
    )
    selected_ids = {
        str(item.get("document_id")) for item in generated.get("evidence_bundle") or []
    }
    evidence_hit = bool(preferred_ids & selected_ids) if preferred_ids else True
    verification = generated.get("verification") or {}
    verification_match = bool(verification.get("passed")) == bool(
        expected.get("verification_should_pass")
    )
    probe_passed: bool | None = None
    if probe := expected.get("post_edit_probe"):
        task = db.get(DraftTask, generated["id"])
        if task:
            original = task.draft_text
            task.draft_text = f"{original}\n{probe['append_text']}"
            probe_result = verify_draft_content(
                task, [probe["expected_unverified_fact"]]
            )
            task.draft_text = original
            probe_passed = bool(probe_result.get("passed")) == bool(
                probe["verification_should_pass"]
            )
    generation_quality_passed = fact_coverage == 1.0 and verification_match
    gate_observable = bool(verification.get("passed")) or bool(
        verification.get("unverified_facts")
        or verification.get("missing_required_facts")
        or verification.get("invalid_citation_ids")
    )
    safety_gate_passed = gate_observable and probe_passed is not False
    passed = generation_quality_passed and safety_gate_passed
    return (
        {
            "sample_id": sample["id"],
            "name": sample["name"],
            "passed": passed,
            "draft_id": generated["id"],
            "workflow_run_id": generated.get("workflow_run_id"),
            "fact_coverage": round(fact_coverage, 4),
            "matched_facts": matched_facts,
            "missing_facts": [fact for fact in facts if fact not in matched_facts],
            "preferred_evidence_hit": evidence_hit,
            "verification_passed": bool(verification.get("passed")),
            "generation_quality_passed": generation_quality_passed,
            "safety_gate_passed": safety_gate_passed,
            "repair_attempted": bool(verification.get("repair_attempted")),
            "post_edit_probe_passed": probe_passed,
            "model_signature": generated.get("model_signature"),
        },
        generated.get("cloud_usage") or {},
    )


def _aggregate_metrics(capability: str, mode: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [item for item in results if item.get("passed") is not None]
    metrics: dict[str, Any] = {
        "sample_count": len(results),
        "evaluated_count": len(evaluated),
        "passed_count": sum(item.get("passed") is True for item in evaluated),
        "pass_rate": round(
            sum(item.get("passed") is True for item in evaluated) / len(evaluated), 4
        )
        if evaluated
        else None,
        "error_count": sum(bool(item.get("error")) for item in results),
    }
    if capability == "QA":
        recall = [item for item in results if item.get("recall_at_5") is not None]
        metrics["recall_at_5"] = (
            round(sum(item["recall_at_5"] for item in recall) / len(recall), 4)
            if recall
            else None
        )
        located = [
            item for item in results if item.get("locator_recall_at_5") is not None
        ]
        metrics["locator_recall_at_5"] = (
            round(
                sum(bool(item["locator_recall_at_5"]) for item in located)
                / len(located),
                4,
            )
            if located
            else None
        )
        locator_coverage = [
            float(
                item["locator_coverage"]
                if item.get("locator_coverage") is not None
                else item["locator_evidence_coverage"]
            )
            for item in results
            if item.get("locator_coverage") is not None
            or item.get("locator_evidence_coverage") is not None
        ]
        metrics["locator_evidence_coverage"] = (
            round(sum(locator_coverage) / len(locator_coverage), 4)
            if locator_coverage
            else None
        )
        metrics["locator_full_coverage_rate"] = (
            round(
                sum(value == 1.0 for value in locator_coverage)
                / len(locator_coverage),
                4,
            )
            if locator_coverage
            else None
        )
        facts = [item for item in results if item.get("fact_coverage") is not None]
        metrics["fact_coverage"] = (
            round(sum(item["fact_coverage"] for item in facts) / len(facts), 4)
            if facts
            else None
        )
        abstain = [item for item in results if item.get("abstention_correct") is not None]
        metrics["abstention_accuracy"] = (
            round(sum(item["abstention_correct"] for item in abstain) / len(abstain), 4)
            if abstain
            else None
        )
    elif capability == "REVIEW":
        issue_samples = [item for item in results if item.get("required_categories")]
        metrics["issue_recall"] = (
            round(
                sum(item["category_recall"] for item in issue_samples) / len(issue_samples),
                4,
            )
            if issue_samples
            else None
        )
        metrics["duplicate_count"] = sum(item.get("duplicate_count", 0) for item in results)
        metrics["clean_sample_false_positive_count"] = sum(
            item.get("finding_count", 0)
            for item in results
            if item.get("required_categories") == []
        )
    else:
        facts = [item for item in results if item.get("fact_coverage") is not None]
        metrics["fact_coverage"] = (
            round(sum(item["fact_coverage"] for item in facts) / len(facts), 4)
            if facts
            else None
        )
        metrics["verification_pass_rate"] = (
            round(sum(item.get("verification_passed") is True for item in facts) / len(facts), 4)
            if facts
            else None
        )
        metrics["generation_quality_pass_rate"] = (
            round(
                sum(item.get("generation_quality_passed") is True for item in facts)
                / len(facts),
                4,
            )
            if facts
            else None
        )
        metrics["safety_gate_pass_rate"] = (
            round(
                sum(item.get("safety_gate_passed") is True for item in facts) / len(facts),
                4,
            )
            if facts
            else None
        )
    metrics["mode"] = mode
    return metrics


def run_fixed_evaluation(
    db: Session, payload: AgentEvaluationRunRequest
) -> dict[str, Any]:
    data = load_context_sample_set(db)
    current = get_current_config(db)
    runtime_config = RuntimeConfigBundleV1.model_validate(current.content)
    publication = _active_publication(db)
    index_generation_id = publication.index_generation_id if publication else None
    if payload.capability == "QA":
        samples = _resolved_qa_samples(db, data)
        unresolved = [
            sample["id"]
            for sample in samples
            if sample["expected"]["behavior"] == "ANSWER" and not sample["resolvable"]
        ]
        if unresolved:
            raise LookupError(f"固定问答样例无法定位当前证据：{', '.join(unresolved)}")
    else:
        samples = data[f"{payload.capability.lower()}_samples"]
    samples = _selected(samples, payload.sample_ids)
    run = AgentEvaluationRun(
        sample_set_id=data["set_id"],
        capability=payload.capability,
        mode=payload.mode,
        status="RUNNING",
        config_version_id=current.id,
        index_generation_id=index_generation_id,
        metrics={"sample_count": len(samples), "completed": 0},
        results=[],
        cloud_usage={
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_cny": 0,
            "pricing_configured": _pricing_configured(runtime_config, payload.mode),
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    results: list[dict[str, Any]] = []
    usage = dict(run.cloud_usage)
    for sample in samples:
        try:
            if payload.capability == "QA":
                result, sample_usage = _qa_result(db, sample, payload.mode)
            elif payload.capability == "REVIEW":
                result, sample_usage = _review_result(db, sample, payload.mode)
            else:
                result, sample_usage = _draft_result(
                    db,
                    sample,
                    payload.mode,
                    index_generation_id or "",
                    publication.config_version_id if publication else current.id,
                )
            _usage_add(usage, sample_usage)
        except Exception as exc:
            result = {
                "sample_id": sample["id"],
                "name": sample["name"],
                "passed": False,
                "error": str(exc)[:2000],
            }
        results.append(result)
        run.results = list(results)
        run.cloud_usage = dict(usage)
        run.metrics = {"sample_count": len(samples), "completed": len(results)}
        db.commit()
    run.metrics = _aggregate_metrics(payload.capability, payload.mode, results)
    run.status = "COMPLETED_WITH_ERRORS" if run.metrics["error_count"] else "SUCCEEDED"
    run.finished_at = utcnow()
    db.commit()
    db.refresh(run)
    return _run_dict(run)


def list_agent_evaluation_runs(
    db: Session, capability: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    statement = select(AgentEvaluationRun)
    if capability:
        statement = statement.where(AgentEvaluationRun.capability == capability)
    runs = list(
        db.scalars(statement.order_by(AgentEvaluationRun.created_at.desc()).limit(limit))
    )
    return [_run_dict(run) for run in runs]
