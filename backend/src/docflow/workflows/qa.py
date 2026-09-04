from __future__ import annotations

import re
import time
from collections.abc import Callable
from importlib.metadata import version
from typing import Any, TypedDict

from langchain_core.documents import Document as LangChainDocument
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from docflow.db.models import Document, Page, SourceFile, WorkflowRun, utcnow
from docflow.domain.config import RuntimeConfigBundleV1
from docflow.domain.retrieval import RetrievalAnswerRequest, RetrievalSearchRequest
from docflow.services.config_service import get_current_config
from docflow.services.model_gateway import CloudModelError, generate_chat_answer
from docflow.services.retrieval import (
    _artifact_url,
    _has_explicit_no_objection,
    _query_centered_snippet,
    generate_extractive_answer,
    rerank_retrieval_results,
    search,
)


class QAState(TypedDict, total=False):
    question: str
    mode: str
    limit: int
    evidence_limit: int
    index_generation_id: str | None
    query_intent: str
    rewritten_query: str
    request_payload: dict[str, Any]
    retrieval: dict[str, Any]
    evidence_sufficient: bool
    evidence_score: float
    evidence_reasons: list[str]
    answer: str
    answer_mode: str
    confidence: float
    citations: list[dict[str, Any]]
    case_ids: list[str]
    verification: dict[str, Any]
    generation_model_signature: str
    generation_warning: str | None
    cloud_usage: dict[str, int]
    generation_config_version_id: str


_NODE_LABELS = {
    "understand_query": "问题理解",
    "rewrite_query": "检索查询改写",
    "retrieve_evidence": "混合证据召回",
    "rerank_evidence": "候选证据重排序",
    "assess_evidence": "证据充分性判断",
    "generate_answer": "答案生成",
    "insufficient_answer": "无证据安全回答",
    "verify_citations": "引用一致性校验",
}


def classify_intent(question: str) -> str:
    if re.search(r"哪些(?:单位|部门)|回复单位|复函单位", question):
        return "ORGANIZATION_AGGREGATION"
    if re.search(r"多少|金额|费用|预算|单价|价格|日期|时间|比例", question):
        return "NUMERIC_FACT"
    if re.search(r"谁|人员|法定代表人|负责人|联系人", question):
        return "PERSON_FACT"
    return "GENERAL_FACT"


def _prioritize_organization_evidence(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep direct, explicit replies visible for multi-document aggregation."""
    preferred: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for item in results:
        snippet = str(item.get("snippet") or "")
        is_explicit_reply = item.get(
            "document_role"
        ) == "REPLY" and _has_explicit_no_objection(snippet)
        (preferred if is_explicit_reply else remaining).append(item)
    return [*preferred, *remaining]


def _promote_adjacent_candidate_page(
    question: str,
    results: list[dict[str, Any]],
    evidence_limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep one retrieved neighbouring page visible for multi-fact questions.

    The neighbour must already be in the retrieval candidate pool. This avoids adding
    unrelated pages while preventing a reranker from separating consecutive evidence
    pages before answer generation.
    """
    clauses = [
        value
        for value in re.split(r"[，,；;]", question)
        if re.search(r"多少|几(?:个|项|年|月|日)|金额|费用|日期|比例|分别", value)
    ]
    if len(clauses) < 2 or evidence_limit < 2:
        return results, []

    visible = results[:evidence_limit]
    visible_ids = {str(item.get("page_id")) for item in visible}
    candidate_positions = {str(item.get("page_id")): index for index, item in enumerate(results)}
    for anchor in visible:
        document_id = str(anchor.get("document_id") or "")
        page_number = anchor.get("page_number")
        if not document_id or not isinstance(page_number, int):
            continue
        neighbours = [
            item
            for item in results[evidence_limit:]
            if str(item.get("document_id") or "") == document_id
            and str(item.get("page_id")) not in visible_ids
            and isinstance(item.get("page_number"), int)
            and abs(int(item["page_number"]) - page_number) == 1
        ]
        if not neighbours:
            continue
        neighbour = min(
            neighbours,
            key=lambda item: candidate_positions[str(item.get("page_id"))],
        )
        promoted_id = str(neighbour.get("page_id"))
        ordered: list[dict[str, Any]] = []
        for item in results:
            if str(item.get("page_id")) == promoted_id:
                continue
            ordered.append(item)
            if str(item.get("page_id")) == str(anchor.get("page_id")):
                ordered.append(neighbour)
        return ordered, [promoted_id]
    return results, []


def _append_adjacent_page_candidates(
    db: Session,
    question: str,
    results: list[dict[str, Any]],
    evidence_limit: int,
) -> list[dict[str, Any]]:
    """Materialize neighbouring pages for strong multi-fact page hits.

    Only pages adjacent to an already selected candidate are added, and normal
    per-document limits still apply in the following selection step.
    """
    clauses = [
        value
        for value in re.split(r"[，,；;]", question)
        if re.search(r"多少|几(?:个|项|年|月|日)|金额|费用|日期|比例|分别", value)
    ]
    if len(clauses) < 2 or evidence_limit < 2:
        return results
    known_page_ids = {str(item.get("page_id")) for item in results}
    appended = list(results)
    for anchor in results[:evidence_limit]:
        document_id = str(anchor.get("document_id") or "")
        page_number = anchor.get("page_number")
        if not document_id or not isinstance(page_number, int):
            continue
        document = db.get(Document, document_id)
        if document is None:
            continue
        source = db.get(SourceFile, document.source_file_id)
        for adjacent_number in (page_number - 1, page_number + 1):
            if adjacent_number < 1:
                continue
            page = db.scalar(
                select(Page).where(
                    Page.document_id == document_id,
                    Page.page_number == adjacent_number,
                )
            )
            if page is None or page.id in known_page_ids or not page.text.strip():
                continue
            appended.append(
                {
                    "rank": len(appended) + 1,
                    "score": 0.0,
                    "ranking_algorithm": "ADJACENT_CONTEXT",
                    "page_id": page.id,
                    "page_number": page.page_number,
                    "page_type": page.page_type,
                    "document_id": document.id,
                    "title": document.title,
                    "document_number": document.document_number,
                    "case_id": document.case_id,
                    "document_role": document.document_role,
                    "version_role": document.version_role,
                    "authority_score": document.authority_score,
                    "relative_path": source.relative_path if source else None,
                    "snippet": _query_centered_snippet(page.text, question),
                    "preview_url": _artifact_url(page.image_path),
                    "model_signature": "local:adjacent-page-context-v1",
                    "collection": "postgresql:pages",
                    "match_sources": ["adjacent_context"],
                    "visual_score": None,
                    "text_score": None,
                    "semantic_score": None,
                }
            )
            known_page_ids.add(page.id)
    return appended


def _state_snapshot(state: QAState) -> dict[str, Any]:
    retrieval = state.get("retrieval") or {}
    return {
        "question": state.get("question"),
        "mode": state.get("mode"),
        "query_intent": state.get("query_intent"),
        "rewritten_query": state.get("rewritten_query"),
        "index_generation_id": (retrieval.get("context") or {}).get("index_generation_id"),
        "retrieval_count": retrieval.get("total", 0),
        "evidence_sufficient": state.get("evidence_sufficient"),
        "evidence_score": state.get("evidence_score"),
        "evidence_reasons": state.get("evidence_reasons") or [],
        "answer_mode": state.get("answer_mode"),
        "generation_model_signature": state.get("generation_model_signature"),
        "generation_config_version_id": state.get("generation_config_version_id"),
        "generation_warning": state.get("generation_warning"),
        "cloud_usage": state.get("cloud_usage") or {},
        "confidence": state.get("confidence"),
        "citation_count": len(state.get("citations") or []),
        "verification": state.get("verification") or {},
    }


def _step_summary(node_name: str, update: QAState) -> str:
    if node_name == "understand_query":
        return f"识别意图：{update.get('query_intent', 'UNKNOWN')}"
    if node_name == "rewrite_query":
        return f"检索表达：{update.get('rewritten_query', '')}"
    if node_name == "retrieve_evidence":
        retrieval = update.get("retrieval") or {}
        return f"召回 {retrieval.get('total', 0)} 个候选页面"
    if node_name == "rerank_evidence":
        retrieval = update.get("retrieval") or {}
        reranker = (retrieval.get("debug") or {}).get("reranker") or {}
        return (
            f"{reranker.get('model_signature')} 重排 {reranker.get('candidate_count', 0)} 条"
            if reranker.get("applied")
            else "未配置 Reranker，保留融合排序"
        )
    if node_name == "assess_evidence":
        label = "证据可用于回答" if update.get("evidence_sufficient") else "证据不足"
        return f"{label} · 评分 {float(update.get('evidence_score') or 0):.2f}"
    if node_name in {"generate_answer", "insufficient_answer"}:
        return f"生成 {len(update.get('citations') or [])} 条引用"
    verification = update.get("verification") or {}
    return "引用与页面来源一致" if verification.get("citations_valid") else "引用校验未通过"


def _workflow_metadata(run: WorkflowRun) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "workflow_type": run.workflow_type,
        "status": run.status,
        "engine": run.engine,
        "engine_version": run.engine_version,
        "trace": run.trace_json,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def run_qa_workflow(db: Session, request: RetrievalAnswerRequest) -> dict[str, Any]:
    try:
        engine_version = version("langgraph")
    except Exception:  # pragma: no cover - package metadata is present in normal installs
        engine_version = "unknown"
    generation_version = get_current_config(db)
    generation_config = RuntimeConfigBundleV1.model_validate(generation_version.content)
    run = WorkflowRun(
        workflow_type="RETRIEVAL_QA",
        status="RUNNING",
        input_json=request.model_dump(mode="json"),
        config_version_id=generation_version.id,
        engine="langgraph-stategraph",
        engine_version=engine_version,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    trace: list[dict[str, Any]] = []
    current_state: QAState = {
        "question": request.query,
        "mode": request.mode,
        "limit": request.limit,
        "evidence_limit": request.evidence_limit,
        "index_generation_id": request.index_generation_id,
        "generation_config_version_id": generation_version.id,
        "request_payload": request.model_dump(mode="json"),
    }

    def persist_step(node_name: str, update: QAState, duration_ms: int) -> None:
        current_state.update(update)
        trace.append(
            {
                "sequence": len(trace) + 1,
                "node": node_name,
                "label": _NODE_LABELS[node_name],
                "status": "SUCCEEDED",
                "duration_ms": duration_ms,
                "summary": _step_summary(node_name, update),
            }
        )
        run.trace_json = list(trace)
        run.state_json = _state_snapshot(current_state)
        retrieval = current_state.get("retrieval") or {}
        context = retrieval.get("context") or {}
        run.index_generation_id = context.get("index_generation_id")
        db.commit()

    def traced(
        node_name: str,
        function: Callable[[QAState], QAState],
    ) -> Callable[[QAState], QAState]:
        def invoke(state: QAState) -> QAState:
            started = time.perf_counter()
            try:
                update = function(state)
            except Exception:
                trace.append(
                    {
                        "sequence": len(trace) + 1,
                        "node": node_name,
                        "label": _NODE_LABELS[node_name],
                        "status": "FAILED",
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                        "summary": "节点执行失败",
                    }
                )
                run.trace_json = list(trace)
                db.commit()
                raise
            persist_step(
                node_name,
                update,
                round((time.perf_counter() - started) * 1000),
            )
            return update

        return invoke

    def understand_query(state: QAState) -> QAState:
        return {"query_intent": classify_intent(state["question"])}

    def rewrite_query(state: QAState) -> QAState:
        value = re.sub(
            r"^(?:请问|麻烦|请帮我|帮我|我想知道|查一下|查询一下|搜索一下)[，,：:\s]*",
            "",
            state["question"].strip(),
        )
        value = re.sub(r"\s+", " ", value).strip(" ，,。？?")
        if state.get("query_intent") == "ORGANIZATION_AGGREGATION" and "回复" not in value:
            value = f"{value} 回复 复函 答复单位"
        return {"rewritten_query": value or state["question"]}

    def retrieve_evidence(state: QAState) -> QAState:
        candidate_limit = min(50, max(int(state["limit"]) * 3, 20))
        retrieval = search(
            db,
            RetrievalSearchRequest.model_validate(
                {
                    **state["request_payload"],
                    "query": state.get("rewritten_query") or state["question"],
                    "limit": candidate_limit,
                    "rerank": False,
                    "debug": True,
                }
            ),
        )
        return {"retrieval": retrieval}

    def rerank_evidence(state: QAState) -> QAState:
        if not bool(state["request_payload"].get("rerank", True)):
            retrieval = state["retrieval"]
        else:
            retrieval = rerank_retrieval_results(
                generation_config,
                state.get("rewritten_query") or state["question"],
                state["retrieval"],
            )
        results = list(retrieval.get("results") or [])
        if state.get("query_intent") == "ORGANIZATION_AGGREGATION":
            results = _prioritize_organization_evidence(results)
        results = _append_adjacent_page_candidates(
            db,
            state["question"],
            results,
            int(state["evidence_limit"]),
        )
        results, promoted_page_ids = _promote_adjacent_candidate_page(
            state["question"],
            results,
            int(state["evidence_limit"]),
        )
        selected: list[dict[str, Any]] = []
        deferred: list[dict[str, Any]] = []
        document_counts: dict[str, int] = {}
        for item in results:
            document_id = str(item["document_id"])
            if document_counts.get(document_id, 0) >= 2:
                deferred.append(item)
                continue
            selected.append(item)
            document_counts[document_id] = document_counts.get(document_id, 0) + 1
            if len(selected) >= int(state["limit"]):
                break
        selected.extend(deferred[: max(0, int(state["limit"]) - len(selected))])
        selected = [
            {**item, "rank": rank}
            for rank, item in enumerate(selected[: int(state["limit"])], start=1)
        ]
        return {
            "retrieval": {
                **retrieval,
                "results": selected,
                "total": len(selected),
                "debug": {
                    **(retrieval.get("debug") or {}),
                    "context_expansion": {
                        "strategy": "retrieved-adjacent-page-v1",
                        "promoted_page_ids": promoted_page_ids,
                    },
                },
            }
        }

    def assess_evidence(state: QAState) -> QAState:
        results = (state.get("retrieval") or {}).get("results") or []
        has_textual_evidence = any(str(item.get("snippet") or "").strip() for item in results)
        reasons: list[str] = []
        score = 0.0
        if results:
            score += 0.25
            reasons.append("存在候选页面")
        if has_textual_evidence:
            score += 0.35
            reasons.append("存在可引用文本")
        if any(len(item.get("match_sources") or []) > 1 for item in results[:5]):
            score += 0.2
            reasons.append("多个检索分支交叉命中")
        if any(item.get("rerank_score") is not None for item in results[:5]):
            score += 0.2
            reasons.append("已完成语义重排序")
        score = round(min(1.0, score), 2)
        return {
            "evidence_sufficient": bool(score >= 0.55 and has_textual_evidence),
            "evidence_score": score,
            "evidence_reasons": reasons,
        }

    def generate_answer(state: QAState) -> QAState:
        retrieval = state["retrieval"]
        # LangChain Document 作为召回证据和生成节点之间的标准边界；后续可直接接 Reranker/LLM。
        evidence_documents = [
            LangChainDocument(
                page_content=str(item.get("snippet") or ""),
                metadata={
                    "page_id": item["page_id"],
                    "document_id": item["document_id"],
                    "rank": item["rank"],
                },
            )
            for item in retrieval["results"]
        ]
        normalized_retrieval = {
            **retrieval,
            "results": [
                {**item, "snippet": document.page_content}
                for item, document in zip(retrieval["results"], evidence_documents, strict=True)
            ],
        }
        retrieval_usage = dict(retrieval.get("cloud_usage") or {})
        if state.get("query_intent") == "ORGANIZATION_AGGREGATION":
            extracted = generate_extractive_answer(
                state["question"], normalized_retrieval, state["evidence_limit"]
            )
            if extracted.get("citations"):
                return {
                    **extracted,
                    "generation_model_signature": "local:organization-aggregation-v1",
                    "generation_warning": None,
                    "cloud_usage": retrieval_usage,
                }
        if generation_config.routing.qa_generation_primary:
            try:
                cloud = generate_chat_answer(
                    generation_config,
                    state["question"],
                    normalized_retrieval["results"],
                    state["evidence_limit"],
                )
                usage = {
                    key: int(retrieval_usage.get(key, 0)) + int(cloud.usage.get(key, 0))
                    for key in {"calls", "input_tokens", "output_tokens"}
                }
                return {
                    "answer": cloud.answer,
                    "answer_mode": "DEEPSEEK_GENERATIVE",
                    "confidence": cloud.confidence,
                    "citations": cloud.citations,
                    "generation_model_signature": cloud.model_signature,
                    "generation_warning": None,
                    "cloud_usage": usage,
                }
            except CloudModelError as exc:
                generated = generate_extractive_answer(
                    state["question"],
                    normalized_retrieval,
                    state["evidence_limit"],
                )
                usage = {
                    key: int(retrieval_usage.get(key, 0)) + int(exc.usage.get(key, 0))
                    for key in {"calls", "input_tokens", "output_tokens"}
                }
                return {
                    **generated,
                    "generation_model_signature": "local:extractive-v1",
                    "generation_warning": f"云端对话模型暂时不可用，已降级为本地抽取：{exc}",
                    "cloud_usage": usage,
                }
        generated = generate_extractive_answer(
            state["question"],
            normalized_retrieval,
            state["evidence_limit"],
        )
        return {
            **generated,
            "generation_model_signature": "local:extractive-v1",
            "generation_warning": None,
            "cloud_usage": retrieval_usage,
        }

    def insufficient_answer(state: QAState) -> QAState:
        return {
            "answer": "当前知识库中没有检索到足够证据，暂时无法回答。",
            "answer_mode": "SAFE_REFUSAL",
            "confidence": 0.0,
            "citations": [],
            "generation_model_signature": "local:safe-refusal-v1",
            "generation_warning": None,
            "cloud_usage": (state.get("retrieval") or {}).get("cloud_usage") or {},
        }

    def verify_citations(state: QAState) -> QAState:
        results = (state.get("retrieval") or {}).get("results") or []
        valid_page_ids = {str(item["page_id"]) for item in results}
        citations = state.get("citations") or []
        citations_valid = all(str(item.get("page_id")) in valid_page_ids for item in citations)
        answer_grounded = not citations or all(
            f"[{item['id']}]" in state.get("answer", "") for item in citations
        )
        inline_ids = {int(value) for value in re.findall(r"\[(\d+)\]", state.get("answer", ""))}
        citation_ids = {int(item["id"]) for item in citations}
        inline_citations_resolved = inline_ids.issubset(citation_ids)
        confidence = float(state.get("confidence") or 0.0)
        if not citations_valid or not answer_grounded or not inline_citations_resolved:
            confidence = min(confidence, 0.35)
        case_ids = sorted(
            {str(item["case_id"]) for item in citations if item.get("case_id")}
        )
        return {
            "case_ids": case_ids,
            "confidence": confidence,
            "verification": {
                "citations_valid": citations_valid,
                "answer_grounded": answer_grounded,
                "inline_citations_resolved": inline_citations_resolved,
                "unresolved_citation_ids": sorted(inline_ids - citation_ids),
                "citation_count": len(citations),
            },
        }

    graph = StateGraph(QAState)
    graph.add_node("understand_query", traced("understand_query", understand_query))
    graph.add_node("rewrite_query", traced("rewrite_query", rewrite_query))
    graph.add_node("retrieve_evidence", traced("retrieve_evidence", retrieve_evidence))
    graph.add_node("rerank_evidence", traced("rerank_evidence", rerank_evidence))
    graph.add_node("assess_evidence", traced("assess_evidence", assess_evidence))
    graph.add_node("generate_answer", traced("generate_answer", generate_answer))
    graph.add_node("insufficient_answer", traced("insufficient_answer", insufficient_answer))
    graph.add_node("verify_citations", traced("verify_citations", verify_citations))
    graph.add_edge(START, "understand_query")
    graph.add_edge("understand_query", "rewrite_query")
    graph.add_edge("rewrite_query", "retrieve_evidence")
    graph.add_edge("retrieve_evidence", "rerank_evidence")
    graph.add_edge("rerank_evidence", "assess_evidence")
    graph.add_conditional_edges(
        "assess_evidence",
        lambda state: "generate_answer" if state["evidence_sufficient"] else "insufficient_answer",
        {
            "generate_answer": "generate_answer",
            "insufficient_answer": "insufficient_answer",
        },
    )
    graph.add_edge("generate_answer", "verify_citations")
    graph.add_edge("insufficient_answer", "verify_citations")
    graph.add_edge("verify_citations", END)

    try:
        final_state = graph.compile().invoke(current_state)
        retrieval = final_state["retrieval"]
        response = {
            "question": request.query,
            "rewritten_query": final_state.get("rewritten_query") or request.query,
            "answer": final_state["answer"],
            "answer_mode": final_state["answer_mode"],
            "confidence": final_state["confidence"],
            "citations": final_state.get("citations") or [],
            "case_ids": final_state.get("case_ids") or [],
            "verification": final_state.get("verification") or {},
            "evidence_assessment": {
                "sufficient": final_state.get("evidence_sufficient", False),
                "score": final_state.get("evidence_score", 0.0),
                "reasons": final_state.get("evidence_reasons") or [],
            },
            "generation_model_signature": final_state.get("generation_model_signature"),
            "generation_warning": final_state.get("generation_warning"),
            "cloud_usage": final_state.get("cloud_usage") or {},
            "generation_config_version_id": generation_version.id,
            "retrieval": retrieval,
        }
        run.status = "SUCCEEDED"
        run.finished_at = utcnow()
        run.output_json = {key: value for key, value in response.items() if key != "retrieval"}
        run.state_json = _state_snapshot(final_state)
        db.commit()
        db.refresh(run)
        return {**response, "workflow": _workflow_metadata(run)}
    except Exception as exc:
        run.status = "FAILED"
        run.error_message = str(exc)[:4000]
        run.finished_at = utcnow()
        run.trace_json = list(trace)
        db.commit()
        raise
