from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from docflow.db.models import (
    ConfigVersion,
    Document,
    IngestionJob,
    Page,
    QaEvaluationRun,
    QaEvaluationSample,
    SourceFile,
    utcnow,
)
from docflow.domain.qa_evaluation import QaEvaluationSampleUpdate
from docflow.domain.retrieval import RetrievalAnswerRequest, RetrievalSearchRequest
from docflow.services.retrieval import _resolve_context, _tokens, answer, search


def _sample_dict(sample: QaEvaluationSample) -> dict[str, Any]:
    return {
        "id": sample.id,
        "index_generation_id": sample.index_generation_id,
        "question": sample.question,
        "reference_answer": sample.reference_answer,
        "answer_aliases": sample.answer_aliases or [],
        "expected_page_ids": sample.expected_page_ids or [],
        "expected_document_ids": sample.expected_document_ids or [],
        "category": sample.category,
        "status": sample.status,
        "source": sample.source,
        "notes": sample.notes,
        "created_at": sample.created_at.isoformat(),
        "updated_at": sample.updated_at.isoformat(),
    }


def _run_dict(run: QaEvaluationRun, include_results: bool = True) -> dict[str, Any]:
    return {
        "id": run.id,
        "index_generation_id": run.index_generation_id,
        "config_version_id": run.config_version_id,
        "status": run.status,
        "metrics": run.metrics or {},
        "results": run.results or [] if include_results else [],
        "cloud_usage": run.cloud_usage or {},
        "error_message": run.error_message,
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def list_samples(db: Session) -> dict[str, Any]:
    context = _resolve_context(db)
    samples = list(
        db.scalars(
            select(QaEvaluationSample)
            .where(QaEvaluationSample.index_generation_id == context.index_generation_id)
            .order_by(QaEvaluationSample.category, QaEvaluationSample.created_at)
        )
    )
    return {
        "context": {
            "config_version_id": context.config_version_id,
            "index_generation_id": context.index_generation_id,
            "source": context.source,
        },
        "total": len(samples),
        "status_counts": dict(
            (status, sum(1 for item in samples if item.status == status))
            for status in {item.status for item in samples}
        ),
        "samples": [_sample_dict(sample) for sample in samples],
    }


def _first_fact(text: str) -> str:
    for segment in re.split(r"[。！？!?；;\n]+", text):
        compact = re.sub(r"\s+", " ", segment).strip(" ，,：:")
        if 15 <= len(compact) <= 220:
            return compact
    return re.sub(r"\s+", " ", text).strip()[:220]


def _fact_context(text: str, value: str, maximum: int = 34) -> str:
    compact = re.sub(r"\s+", " ", text)
    position = compact.find(value)
    if position < 0:
        return "相关字段"
    start = max(0, position - maximum)
    end = min(len(compact), position + len(value) + 10)
    context = compact[start:end].replace(value, "____").strip(" ，,。；;：:")
    return context or "相关字段"


def generate_samples(db: Session, target_count: int, replace: bool) -> dict[str, Any]:
    context = _resolve_context(db)
    existing = list(
        db.scalars(
            select(QaEvaluationSample).where(
                QaEvaluationSample.index_generation_id == context.index_generation_id
            )
        )
    )
    if existing and not replace:
        return list_samples(db)
    if existing:
        db.execute(
            delete(QaEvaluationSample).where(
                QaEvaluationSample.index_generation_id == context.index_generation_id
            )
        )
        db.commit()

    rows = db.execute(
        select(Document, Page)
        .join(Page, Page.document_id == Document.id)
        .join(SourceFile, Document.source_file_id == SourceFile.id)
        .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
        .where(
            IngestionJob.index_generation_id == context.index_generation_id,
            Document.config_version_id == context.config_version_id,
        )
        .order_by(Document.authority_score.desc(), Document.title, Page.page_number)
    ).all()
    by_document: dict[str, list[tuple[Document, Page]]] = defaultdict(list)
    for document, page in rows:
        by_document[document.id].append((document, page))
    equivalence_groups: dict[str, list[str]] = defaultdict(list)
    for document_id, document_pages in by_document.items():
        document = document_pages[0][0]
        identity = _normalized(document.document_number or document.title)
        equivalence_groups[identity].append(document_id)
    equivalent_ids = {
        document_id: equivalence_groups[_normalized(document.document_number or document.title)]
        for document_id, document_pages in by_document.items()
        for document in [document_pages[0][0]]
    }

    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for document_id, document_pages in by_document.items():
        document = document_pages[0][0]
        title = re.sub(r"\s+", "", document.title or "未命名文档")[:70]
        first_page = document_pages[0][1]
        all_text = "\n".join(page.text for _, page in document_pages if page.text)
        if document.document_number:
            pools["DOCUMENT_NUMBER"].append(
                {
                    "question": f"《{title}》的文号是什么？",
                    "reference_answer": document.document_number,
                    "answer_aliases": [document.document_number],
                    "expected_page_ids": [first_page.id],
                    "expected_document_ids": equivalent_ids[document_id],
                }
            )
        person = re.search(r"法定代表人\s*[:：]?\s*([^\s，。；;：:]{2,8})", all_text)
        if person:
            value = person.group(1)
            page = next(page for _, page in document_pages if value in page.text)
            context_text = _fact_context(page.text, value)
            pools["PERSON"].append(
                {
                    "question": f"《{title}》中“{context_text}”对应的法定代表人是谁？",
                    "reference_answer": value,
                    "answer_aliases": [value, f"法定代表人{value}"],
                    "expected_page_ids": [page.id],
                    "expected_document_ids": [document_id],
                }
            )
        money = re.search(r"\d[\d,]*(?:\.\d+)?\s*(?:亿元|万元|元(?:/年)?|%)", all_text)
        if money:
            value = re.sub(r"\s+", "", money.group(0))
            page = next(page for _, page in document_pages if money.group(0) in page.text)
            context_text = _fact_context(page.text, money.group(0))
            pools["NUMERIC_FACT"].append(
                {
                    "question": f"《{title}》中“{context_text}”对应的金额或比例是多少？",
                    "reference_answer": value,
                    "answer_aliases": [value],
                    "expected_page_ids": [page.id],
                    "expected_document_ids": [document_id],
                }
            )
        date_match = re.search(r"20\d{2}年\d{1,2}月(?:\d{1,2}日)?", all_text)
        if date_match:
            value = date_match.group(0)
            page = next(page for _, page in document_pages if value in page.text)
            context_text = _fact_context(page.text, value)
            pools["DATE_FACT"].append(
                {
                    "question": f"《{title}》中“{context_text}”对应的日期是什么？",
                    "reference_answer": value,
                    "answer_aliases": [value],
                    "expected_page_ids": [page.id],
                    "expected_document_ids": [document_id],
                }
            )
        fact = _first_fact(first_page.text)
        if fact:
            pools["MAIN_TOPIC"].append(
                {
                    "question": f"《{title}》主要说明了什么事项？",
                    "reference_answer": document.title,
                    "answer_aliases": [document.title, fact],
                    "expected_page_ids": [first_page.id],
                    "expected_document_ids": equivalent_ids[document_id],
                }
            )

    selected: list[tuple[str, dict[str, Any]]] = []
    seen_questions: set[str] = set()
    category_order = ["PERSON", "NUMERIC_FACT", "DATE_FACT", "DOCUMENT_NUMBER", "MAIN_TOPIC"]
    while len(selected) < target_count:
        progressed = False
        for category in category_order:
            if not pools[category]:
                continue
            candidate = pools[category].pop(0)
            if candidate["question"] in seen_questions:
                continue
            selected.append((category, candidate))
            seen_questions.add(candidate["question"])
            progressed = True
            if len(selected) >= target_count:
                break
        if not progressed:
            break

    for category, candidate in selected:
        db.add(
            QaEvaluationSample(
                index_generation_id=context.index_generation_id,
                category=category,
                status="DRAFT",
                source="AUTO",
                notes="由当前发布文档中的可验证事实自动生成，可按需微调后确认。",
                **candidate,
            )
        )
    db.commit()
    return list_samples(db)


def update_sample(
    db: Session,
    sample_id: str,
    payload: QaEvaluationSampleUpdate,
) -> dict[str, Any]:
    sample = db.get(QaEvaluationSample, sample_id)
    if not sample:
        raise LookupError("评测样本不存在")
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(sample, key, value)
    sample.updated_at = utcnow()
    db.add(sample)
    db.commit()
    db.refresh(sample)
    return _sample_dict(sample)


def _normalized(value: str) -> str:
    return re.sub(r"\W+", "", value.lower())


def _answer_correct(sample: QaEvaluationSample, generated_answer: str) -> bool:
    answer_value = _normalized(generated_answer)
    aliases = sample.answer_aliases or [sample.reference_answer]
    if any(_normalized(alias) in answer_value for alias in aliases if alias):
        return True
    if sample.category == "MAIN_TOPIC":
        reference_tokens = set(_tokens(sample.reference_answer))
        answer_tokens = set(_tokens(generated_answer))
        return bool(reference_tokens) and (
            len(reference_tokens & answer_tokens) / len(reference_tokens) >= 0.3
        )
    return False


def run_evaluation(
    db: Session,
    mode: str,
    sample_ids: list[str],
) -> dict[str, Any]:
    context = _resolve_context(db)
    config_version = db.get(ConfigVersion, context.config_version_id)
    if not config_version:
        raise LookupError("评测索引关联的配置不存在")
    statement = select(QaEvaluationSample).where(
        QaEvaluationSample.index_generation_id == context.index_generation_id,
        QaEvaluationSample.status != "DISABLED",
    )
    if sample_ids:
        statement = statement.where(QaEvaluationSample.id.in_(sample_ids))
    samples = list(db.scalars(statement.order_by(QaEvaluationSample.created_at)))
    if not samples:
        raise LookupError("没有可运行的评测样本")
    run = QaEvaluationRun(
        index_generation_id=context.index_generation_id,
        config_version_id=config_version.id,
        status="RUNNING",
        metrics={"sample_count": len(samples), "completed": 0},
        results=[],
        cloud_usage={"calls": 0, "input_tokens": 0, "output_tokens": 0},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    results: list[dict[str, Any]] = []
    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    try:
        for sample in samples:
            if mode == "FULL_QA":
                response = answer(
                    db,
                    RetrievalAnswerRequest(
                        query=sample.question,
                        mode="hybrid",
                        limit=10,
                        evidence_limit=5,
                        debug=True,
                    ),
                )
                retrieval = response["retrieval"]
                generated_answer = response["answer"]
                citation_page_ids = {
                    item["page_id"] for item in response.get("citations") or []
                }
                citation_document_ids = {
                    item["document_id"] for item in response.get("citations") or []
                }
                answer_correct = _answer_correct(sample, generated_answer)
                citation_correct = bool(
                    citation_page_ids & set(sample.expected_page_ids or [])
                )
                if sample.category in {"MAIN_TOPIC", "DOCUMENT_NUMBER"}:
                    citation_correct = citation_correct or bool(
                        citation_document_ids & set(sample.expected_document_ids or [])
                    )
                response_usage = response.get("cloud_usage") or {}
            else:
                retrieval = search(
                    db,
                    RetrievalSearchRequest(
                        query=sample.question,
                        mode="hybrid",
                        limit=10,
                        debug=True,
                    ),
                )
                generated_answer = ""
                citation_page_ids = set()
                answer_correct = None
                citation_correct = None
                response_usage = retrieval.get("cloud_usage") or {}
            top_five = {item["page_id"] for item in retrieval.get("results", [])[:5]}
            recall = bool(top_five & set(sample.expected_page_ids or []))
            for key in usage:
                usage[key] += int(response_usage.get(key, 0))
            results.append(
                {
                    "sample_id": sample.id,
                    "question": sample.question,
                    "category": sample.category,
                    "reference_answer": sample.reference_answer,
                    "generated_answer": generated_answer,
                    "expected_page_ids": sample.expected_page_ids,
                    "retrieved_page_ids": [
                        item["page_id"] for item in retrieval.get("results", [])[:5]
                    ],
                    "citation_page_ids": sorted(citation_page_ids),
                    "recall_at_5": recall,
                    "answer_correct": answer_correct,
                    "citation_correct": citation_correct,
                }
            )
            run.results = list(results)
            run.cloud_usage = dict(usage)
            run.metrics = {"sample_count": len(samples), "completed": len(results)}
            db.commit()
        count = len(results)
        full_results = [item for item in results if item["answer_correct"] is not None]
        run.metrics = {
            "sample_count": count,
            "recall_at_5": round(sum(item["recall_at_5"] for item in results) / count, 4),
            "answer_accuracy": round(
                sum(item["answer_correct"] for item in full_results) / len(full_results), 4
            ) if full_results else None,
            "citation_accuracy": round(
                sum(item["citation_correct"] for item in full_results) / len(full_results), 4
            ) if full_results else None,
        }
        run.status = "SUCCEEDED"
        run.finished_at = utcnow()
        db.commit()
        db.refresh(run)
        return _run_dict(run)
    except Exception as exc:
        run.status = "FAILED"
        run.error_message = str(exc)[:4000]
        run.finished_at = utcnow()
        run.results = list(results)
        run.cloud_usage = dict(usage)
        db.commit()
        raise


def list_runs(db: Session, limit: int = 20) -> list[dict[str, Any]]:
    runs = list(
        db.scalars(select(QaEvaluationRun).order_by(QaEvaluationRun.created_at.desc()).limit(limit))
    )
    return [_run_dict(run) for run in runs]
