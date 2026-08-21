from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from docflow.core.settings import get_settings
from docflow.db.models import (
    Chunk,
    ConfigVersion,
    Document,
    IngestionJob,
    Page,
    Publication,
    SourceFile,
)
from docflow.domain.config import RuntimeConfigBundleV1
from docflow.domain.retrieval import RetrievalAnswerRequest, RetrievalSearchRequest
from docflow.services.config_service import get_current_config
from docflow.services.model_gateway import CloudModelError, embed_query, rerank_documents
from docflow.services.vector_index import search_text_vectors, search_visual_pages


class RetrievalContextError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievalContext:
    config_version_id: str
    index_generation_id: str
    source: str


@dataclass(frozen=True)
class TextSearchHit:
    page_id: str
    document_id: str
    score: float
    snippet: str


@dataclass(frozen=True)
class SemanticSearchHit:
    page_id: str
    document_id: str
    score: float
    snippet: str
    model_signature: str
    collection: str


@dataclass
class FusedHit:
    page_id: str
    document_id: str
    score: float = 0.0
    visual_score: float | None = None
    text_score: float | None = None
    semantic_score: float | None = None
    semantic_signature: str | None = None
    semantic_collection: str | None = None
    snippet: str = ""
    branch_ranks: dict[str, int] | None = None
    rrf_contributions: dict[str, float] | None = None
    pre_rerank_score: float | None = None
    rerank_score: float | None = None
    rerank_signature: str | None = None

    @property
    def match_sources(self) -> list[str]:
        sources: list[str] = []
        if self.visual_score is not None:
            sources.append("visual")
        if self.text_score is not None:
            sources.append("text")
        if self.semantic_score is not None:
            sources.append("semantic")
        return sources


def _resolve_context(
    db: Session, requested_generation_id: str | None = None
) -> RetrievalContext:
    if requested_generation_id:
        job = db.scalar(
            select(IngestionJob)
            .where(IngestionJob.index_generation_id == requested_generation_id)
            .order_by(IngestionJob.created_at.desc())
        )
        if not job:
            raise RetrievalContextError("指定的索引代际不存在")
        return RetrievalContext(job.config_version_id, job.index_generation_id, "REQUESTED_INDEX")

    publication = db.scalar(
        select(Publication)
        .where(Publication.active.is_(True))
        .order_by(Publication.published_at.desc())
    )
    if publication:
        return RetrievalContext(
            publication.config_version_id,
            publication.index_generation_id,
            "ACTIVE_PUBLICATION",
        )

    # Publication 是可选步骤，没有活跃 Publication 时回退到最近一次入库结果。
    job = db.scalar(
        select(IngestionJob)
        .join(SourceFile, SourceFile.job_id == IngestionJob.id)
        .join(Document, Document.source_file_id == SourceFile.id)
        .order_by(IngestionJob.created_at.desc())
        .limit(1)
    )
    if not job:
        raise RetrievalContextError("尚无可检索的解析结果，请先创建并完成一个 M1 入库任务")
    return RetrievalContext(job.config_version_id, job.index_generation_id, "LATEST_INGESTION")


def _artifact_url(image_path: str | None) -> str | None:
    if not image_path:
        return None
    root = get_settings().artifact_root.expanduser().resolve()
    path = Path(image_path).expanduser().resolve()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    return f"/api/v1/artifacts/{relative.as_posix()}"


def _tokens(value: str) -> list[str]:
    """Dependency-free lexical tokens suited to short Chinese administrative queries."""
    groups = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", value.lower())
    tokens: list[str] = []
    for group in groups:
        if re.fullmatch(r"[\u3400-\u9fff]+", group):
            if len(group) == 1:
                tokens.append(group)
            else:
                tokens.extend(group[index : index + 2] for index in range(len(group) - 1))
        else:
            tokens.append(group)
    return tokens


_QUESTION_STOP_TOKENS = {
    "什么",
    "多少",
    "哪些",
    "怎么",
    "是否",
    "请问",
    "一下",
    "情况",
    "有关",
    "相关",
}


def _query_terms(query: str) -> set[str]:
    terms = set(_tokens(query))
    return {
        term
        for term in terms
        if term not in _QUESTION_STOP_TOKENS
        and not any(term in stop for stop in _QUESTION_STOP_TOKENS)
    }


def _query_entities(query: str) -> set[str]:
    entities = set(
        re.findall(
            r"[\u3400-\u9fff]{2,}?(?:有限责任公司|有限公司|公司|人民政府|"
            r"管理中心|办公室|管理所|分局)",
            query,
        )
    )
    entities.update(re.findall(r"[\w〔〕［］【】（）()《》〈〉-]{4,}号", query))
    return {value for value in entities if len(value) >= 4}


def _segments(text: str) -> list[str]:
    values = re.split(r"[。！？!?；;\n]+", text)
    return [re.sub(r"\s+", " ", value).strip(" ：:,，") for value in values if value.strip()]


def _has_explicit_no_objection(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(re.search(r"无(?:不同|修改)?意见", compact))


def _compact_excerpt(value: str, terms: set[str], maximum: int = 220) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= maximum:
        return value
    positions = [value.find(term) for term in terms if value.find(term) >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - maximum // 3)
    end = min(len(value), start + maximum)
    excerpt = value[start:end]
    return f"{'…' if start else ''}{excerpt}{'…' if end < len(value) else ''}"


def _query_centered_snippet(value: str, query: str, maximum: int = 1200) -> str:
    """优先返回命中查询实体的行，避免长表格永远只截取开头。"""
    value = value.strip()
    if len(value) <= maximum:
        return value
    terms = _query_terms(query)
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if lines:
        scored = [
            (sum(term in line.lower() for term in terms), len(line), index, line)
            for index, line in enumerate(lines)
        ]
        _, _, best_index, best_line = max(scored)
        if any(term in best_line.lower() for term in terms):
            selected = best_line
            offset = 1
            while len(selected) < maximum and (
                best_index - offset >= 0 or best_index + offset < len(lines)
            ):
                for index in (best_index - offset, best_index + offset):
                    if 0 <= index < len(lines):
                        candidate = f"{selected}\n{lines[index]}"
                        if len(candidate) <= maximum:
                            selected = candidate
                offset += 1
            if len(selected) > maximum:
                positions = [selected.lower().find(term) for term in terms]
                positions = [position for position in positions if position >= 0]
                center = min(positions) if positions else 0
                start = max(0, center - maximum // 3)
                return selected[start : start + maximum]
            return selected
    return _compact_excerpt(value, terms, maximum)


def _citation_from_result(
    citation_id: int,
    result: dict[str, Any],
    excerpt: str,
) -> dict[str, Any]:
    return {
        "id": citation_id,
        "page_id": result["page_id"],
        "document_id": result["document_id"],
        "case_id": result["case_id"],
        "title": result["title"],
        "document_number": result["document_number"],
        "page_number": result["page_number"],
        "relative_path": result["relative_path"],
        "excerpt": excerpt,
        "preview_url": result["preview_url"],
        "match_sources": result["match_sources"],
    }


def _organization_answer(
    question: str,
    results: list[dict[str, Any]],
    evidence_limit: int,
) -> tuple[str, list[dict[str, Any]]] | None:
    if not re.search(r"哪些(?:单位|部门)|回复单位|复函单位", question):
        return None
    if not re.search(r"回复|复函|意见|答复", question):
        return None
    organizations: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    suffix = re.compile(
        r"(?:人民政府|党政综合办公室|财政局.*分局|司法局.*分局|"
        r"管理中心|管理所|办公室|分局|中心|公司)$"
    )
    for result in results:
        if result.get("document_role") != "REPLY":
            continue
        snippet = str(result.get("snippet") or "")
        if not _has_explicit_no_objection(snippet):
            continue
        segments = _segments(snippet)[:8]
        for index, line in enumerate(segments):
            candidate = re.sub(r"^[\W\d_]+|[：:]$", "", line).strip()
            if 4 <= len(candidate) <= 40 and suffix.search(candidate) and candidate not in seen:
                if index + 1 < len(segments) and re.fullmatch(
                    r"[（(][^）)]{2,30}(?:管理所|管理中心|办公室|分局|中心|公司)[）)]",
                    segments[index + 1],
                ):
                    candidate = f"{candidate}{segments[index + 1]}"
                organizations.append((candidate, result))
                seen.add(candidate)
                break
        if len(organizations) >= evidence_limit:
            break
    if not organizations:
        return None
    citations = [
        _citation_from_result(index, result, organization)
        for index, (organization, result) in enumerate(organizations, start=1)
    ]
    lines = [
        f"- {organization} [{index}]"
        for index, (organization, _) in enumerate(organizations, start=1)
    ]
    return "检索到的回复单位包括：\n" + "\n".join(lines), citations


def _person_answer(
    question: str,
    results: list[dict[str, Any]],
    evidence_limit: int,
) -> tuple[str, list[dict[str, Any]]] | None:
    if not re.search(r"法定代表人|负责人|联系人(?:是谁|是哪个|有哪些)?", question):
        return None
    people: list[tuple[str | None, str, dict[str, Any]]] = []
    seen: set[tuple[str | None, str]] = set()
    for result in results:
        snippet = str(result.get("snippet") or "")
        person_match = re.search(r"法定代表人\s*([^\s，。；;：:]{2,8})", snippet)
        if not person_match:
            continue
        person = person_match.group(1).strip()
        organization_matches = list(
            re.finditer(
                r"(?:名称|称)\s*([^\n]{4,60}?(?:有限责任公司|有限公司|管理中心|公司))\s*$",
                snippet,
                flags=re.MULTILINE,
            )
        )
        organization = organization_matches[0].group(1).strip() if organization_matches else None
        key = (organization, person)
        if key in seen:
            continue
        seen.add(key)
        people.append((organization, person, result))
        if len(people) >= evidence_limit:
            break
    if not people:
        return None
    citations = [
        _citation_from_result(
            index,
            result,
            f"{organization}：{person}" if organization else f"法定代表人：{person}",
        )
        for index, (organization, person, result) in enumerate(people, start=1)
    ]
    if len(people) == 1:
        organization, person, _ = people[0]
        subject = f"{organization}的" if organization else "该证照的"
        return f"{subject}法定代表人为 {person} [1]。", citations
    lines = [
        f"- {organization or '未识别企业名称'}：{person} [{index}]"
        for index, (organization, person, _) in enumerate(people, start=1)
    ]
    return "检索到多份相关营业执照，其法定代表人分别为：\n" + "\n".join(lines), citations


def generate_extractive_answer(
    question: str,
    retrieval: dict[str, Any],
    evidence_limit: int = 4,
) -> dict[str, Any]:
    results = list(retrieval.get("results") or [])
    if not results:
        return {
            "answer": "当前知识库中没有检索到足够证据，暂时无法回答。",
            "answer_mode": "LOCAL_EXTRACTIVE",
            "confidence": 0.0,
            "citations": [],
        }

    organization_answer = _organization_answer(question, results, evidence_limit)
    if organization_answer:
        answer_text, citations = organization_answer
        return {
            "answer": answer_text,
            "answer_mode": "LOCAL_EXTRACTIVE",
            "confidence": round(min(0.92, 0.62 + 0.06 * len(citations)), 2),
            "citations": citations,
        }

    person_answer = _person_answer(question, results, evidence_limit)
    if person_answer:
        answer_text, citations = person_answer
        return {
            "answer": answer_text,
            "answer_mode": "LOCAL_EXTRACTIVE",
            "confidence": round(min(0.9, 0.62 + 0.055 * len(citations)), 2),
            "citations": citations,
        }

    terms = _query_terms(question)
    wants_number = bool(re.search(r"多少|金额|费用|预算|单价|价格|日期|时间|比例", question))
    wants_person = bool(re.search(r"谁|人员|法定代表人|负责人|联系人", question))
    candidates: list[tuple[float, int, str, dict[str, Any]]] = []
    for result in results:
        for segment in _segments(result.get("snippet", "")):
            segment_terms = set(_tokens(segment))
            overlap = len(terms & segment_terms)
            score = overlap * 2.0 + 1 / max(1, int(result.get("rank", 1)))
            number_pattern = r"\d|[一二三四五六七八九十百千万亿]+(?:元|万元|%|年|月|日)"
            if wants_number and re.search(number_pattern, segment):
                score += 3.0
            if wants_person and re.search(r"法定代表人|负责人|联系人", segment):
                score += 4.0
            if len(result.get("match_sources") or []) > 1:
                score += 0.5
            if overlap == 0 and score < 3.0:
                continue
            candidates.append(
                (
                    score,
                    int(result.get("rank", 1)),
                    _compact_excerpt(segment, terms),
                    result,
                )
            )
    candidates.sort(key=lambda item: (-item[0], item[1], len(item[2])))

    citations: list[dict[str, Any]] = []
    seen_pages: set[str] = set()
    seen_excerpts: set[str] = set()
    best_score = candidates[0][0] if candidates else 0.0
    focused_threshold = best_score * 0.72 if wants_number or wants_person else 0.0
    for candidate_score, _, excerpt, result in candidates:
        if candidate_score < focused_threshold:
            continue
        normalized = re.sub(r"\W+", "", excerpt)
        if not normalized or normalized in seen_excerpts:
            continue
        page_id = str(result["page_id"])
        if page_id in seen_pages:
            continue
        citations.append(_citation_from_result(len(citations) + 1, result, excerpt))
        seen_pages.add(page_id)
        seen_excerpts.add(normalized)
        if len(citations) >= evidence_limit:
            break

    if not citations:
        top = results[0]
        excerpt = _compact_excerpt(top.get("snippet", ""), terms)
        citations = [_citation_from_result(1, top, excerpt)] if excerpt else []
    if not citations:
        return {
            "answer": "检索到了相关页面，但没有足够的可抽取文本。请查看页面原图确认。",
            "answer_mode": "LOCAL_EXTRACTIVE",
            "confidence": 0.35,
            "citations": [],
        }

    if len(citations) == 1:
        answer_text = f"根据检索到的公文：{citations[0]['excerpt']} [1]"
    else:
        answer_text = "根据检索到的公文，相关证据如下：\n" + "\n".join(
            f"- {citation['excerpt']} [{citation['id']}]" for citation in citations
        )
    hybrid_count = sum(1 for item in citations if len(item["match_sources"]) > 1)
    confidence = min(0.92, 0.52 + 0.07 * len(citations) + 0.04 * hybrid_count)
    return {
        "answer": answer_text,
        "answer_mode": "LOCAL_EXTRACTIVE",
        "confidence": round(confidence, 2),
        "citations": citations,
    }


def search_text_pages(
    db: Session,
    context: RetrievalContext,
    query: str,
    limit: int,
    allowed_page_ids: set[str] | None = None,
) -> list[TextSearchHit]:
    statement = (
        select(Chunk, Document, Page, SourceFile)
        .join(Document, Chunk.document_id == Document.id)
        .join(Page, Chunk.page_id == Page.id)
        .join(SourceFile, Document.source_file_id == SourceFile.id)
        .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
        .where(
            IngestionJob.index_generation_id == context.index_generation_id,
            Document.config_version_id == context.config_version_id,
        )
    )
    if allowed_page_ids is not None:
        if not allowed_page_ids:
            return []
        statement = statement.where(Chunk.page_id.in_(allowed_page_ids))
    rows = db.execute(statement).all()
    query_tokens = _tokens(query)
    if not rows or not query_tokens:
        return []

    corpus: list[tuple[Chunk, Document, Page, SourceFile, Counter[str]]] = []
    document_frequency: Counter[str] = Counter()
    for chunk, document, page, source in rows:
        counts = Counter(_tokens(f"{document.title} {document.document_number or ''} {chunk.text}"))
        corpus.append((chunk, document, page, source, counts))
        document_frequency.update(counts.keys())

    corpus_size = len(corpus)
    average_length = sum(sum(counts.values()) for *_, counts in corpus) / corpus_size
    query_counts = Counter(query_tokens)
    query_entities = _query_entities(query)
    normalized_query = "".join(query.lower().split())
    page_hits: dict[str, TextSearchHit] = {}
    wants_table = bool(re.search(r"表格|明细|清单|统计|台账|金额", query))
    wants_reply = bool(re.search(r"回复|复函|批复|答复|回函", query))
    for chunk, document, page, source, counts in corpus:
        length = max(1, sum(counts.values()))
        score = 0.0
        for token, query_frequency in query_counts.items():
            frequency = counts.get(token, 0)
            if frequency == 0:
                continue
            inverse_frequency = math.log(
                1 + (corpus_size - document_frequency[token] + 0.5)
                / (document_frequency[token] + 0.5)
            )
            denominator = frequency + 1.2 * (0.25 + 0.75 * length / average_length)
            score += inverse_frequency * frequency * 2.2 / denominator * query_frequency

        normalized_text = "".join(chunk.text.lower().split())
        normalized_title = "".join(document.title.lower().split())
        if normalized_query and normalized_query in normalized_text:
            score += 8.0
        if normalized_query and normalized_query in normalized_title:
            score += 10.0
        score += 12.0 * sum(
            1 for entity in query_entities if "".join(entity.lower().split()) in normalized_text
        )
        title_tokens = set(_tokens(document.title))
        score += 0.45 * sum(1 for token in query_counts if token in title_tokens)
        path_tokens = set(_tokens(source.relative_path))
        score += 0.55 * sum(1 for token in query_counts if token in path_tokens)
        if wants_table and page.page_type == "TABLE":
            score += 2.0
        if wants_reply and document.document_role == "REPLY":
            score += 2.0
        score += min(1.0, max(0.0, document.authority_score)) * 0.35
        if score <= 0:
            continue
        hit = TextSearchHit(
            page_id=chunk.page_id,
            document_id=document.id,
            score=score,
            snippet=_query_centered_snippet(chunk.text, query),
        )
        previous = page_hits.get(chunk.page_id)
        if previous is None or hit.score > previous.score:
            page_hits[chunk.page_id] = hit
    return sorted(page_hits.values(), key=lambda item: item.score, reverse=True)[:limit]


def _fuse_hits(
    visual_hits: list[Any],
    text_hits: list[TextSearchHit],
    semantic_hits: list[SemanticSearchHit] | None = None,
) -> list[FusedHit]:
    fused: dict[str, FusedHit] = {}
    for rank, hit in enumerate(visual_hits, start=1):
        item = fused.setdefault(hit.page_id, FusedHit(hit.page_id, hit.document_id))
        contribution = 1 / (60 + rank)
        item.score += contribution
        item.visual_score = hit.score
        item.branch_ranks = {**(item.branch_ranks or {}), "visual": rank}
        item.rrf_contributions = {
            **(item.rrf_contributions or {}),
            "visual": contribution,
        }
    for rank, hit in enumerate(text_hits, start=1):
        item = fused.setdefault(hit.page_id, FusedHit(hit.page_id, hit.document_id))
        contribution = 1 / (60 + rank)
        item.score += contribution
        item.text_score = hit.score
        item.snippet = hit.snippet
        item.branch_ranks = {**(item.branch_ranks or {}), "text": rank}
        item.rrf_contributions = {
            **(item.rrf_contributions or {}),
            "text": contribution,
        }
    for rank, hit in enumerate(semantic_hits or [], start=1):
        item = fused.setdefault(hit.page_id, FusedHit(hit.page_id, hit.document_id))
        contribution = 1 / (60 + rank)
        item.score += contribution
        item.semantic_score = hit.score
        item.semantic_signature = hit.model_signature
        item.semantic_collection = hit.collection
        if not item.snippet:
            item.snippet = hit.snippet
        item.branch_ranks = {**(item.branch_ranks or {}), "semantic": rank}
        item.rrf_contributions = {
            **(item.rrf_contributions or {}),
            "semantic": contribution,
        }
    return sorted(fused.values(), key=lambda item: item.score, reverse=True)


def _diversify_hits(hits: list[FusedHit], limit: int) -> list[FusedHit]:
    """Allow two pages per document before filling more duplicates.

    Page-level evaluation showed that a strict one-page-per-document rule hid the exact
    evidence page in multi-page reports, even when the document itself ranked first.
    """
    selected: list[FusedHit] = []
    deferred: list[FusedHit] = []
    document_counts: Counter[str] = Counter()
    for hit in hits:
        if document_counts[hit.document_id] >= 2:
            deferred.append(hit)
            continue
        selected.append(hit)
        document_counts[hit.document_id] += 1
        if len(selected) == limit:
            return selected
    selected.extend(deferred[: limit - len(selected)])
    return selected


def _allowed_page_ids(
    db: Session,
    context: RetrievalContext,
    request: RetrievalSearchRequest,
) -> set[str] | None:
    has_filters = bool(
        request.case_ids
        or request.document_roles
        or request.version_roles
        or request.date_from
        or request.date_to
        or request.min_authority_score is not None
        or request.authoritative_only
    )
    if not has_filters:
        return None
    statement = (
        select(Page.id)
        .join(Document, Page.document_id == Document.id)
        .join(SourceFile, Document.source_file_id == SourceFile.id)
        .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
        .where(
            IngestionJob.index_generation_id == context.index_generation_id,
            Document.config_version_id == context.config_version_id,
        )
    )
    if request.case_ids:
        statement = statement.where(Document.case_id.in_(request.case_ids))
    if request.document_roles:
        statement = statement.where(Document.document_role.in_(request.document_roles))
    if request.version_roles:
        statement = statement.where(Document.version_role.in_(request.version_roles))
    if request.min_authority_score is not None:
        statement = statement.where(Document.authority_score >= request.min_authority_score)
    if request.authoritative_only:
        statement = statement.where(Document.selected.is_(True))
    if request.date_from:
        statement = statement.where(
            SourceFile.modified_at
            >= datetime.combine(request.date_from, time.min, tzinfo=UTC)
        )
    if request.date_to:
        statement = statement.where(
            SourceFile.modified_at
            < datetime.combine(request.date_to, time.min, tzinfo=UTC) + timedelta(days=1)
        )
    return set(db.scalars(statement))


def _filter_hits_by_page(hits: list[Any], allowed_page_ids: set[str] | None) -> list[Any]:
    if allowed_page_ids is None:
        return hits
    return [hit for hit in hits if hit.page_id in allowed_page_ids]


def _merge_usage(*values: dict[str, int]) -> dict[str, int]:
    return {
        key: sum(int(value.get(key, 0)) for value in values)
        for key in {"calls", "input_tokens", "output_tokens"}
    }


def _branch_debug_rows(db: Session, hits: list[Any], limit: int = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rank, hit in enumerate(hits[:limit], start=1):
        page = db.get(Page, hit.page_id)
        document = db.get(Document, hit.document_id)
        if not page or not document:
            continue
        rows.append(
            {
                "rank": rank,
                "page_id": hit.page_id,
                "document_id": hit.document_id,
                "score": float(hit.score),
                "title": document.title,
                "page_number": page.page_number,
            }
        )
    return rows


def retrieval_options(db: Session) -> dict[str, Any]:
    context = _resolve_context(db)
    rows = db.execute(
        select(Document, SourceFile)
        .join(SourceFile, Document.source_file_id == SourceFile.id)
        .join(IngestionJob, SourceFile.job_id == IngestionJob.id)
        .where(
            IngestionJob.index_generation_id == context.index_generation_id,
            Document.config_version_id == context.config_version_id,
        )
        .order_by(Document.authority_score.desc(), Document.title)
    ).all()
    cases: dict[str, int] = Counter(document.case_id for document, _ in rows)
    roles: dict[str, int] = Counter(document.document_role for document, _ in rows)
    versions: dict[str, int] = Counter(document.version_role for document, _ in rows)
    modified_values = [source.modified_at for _, source in rows if source.modified_at]
    presets: list[dict[str, str]] = []
    for document, _ in rows:
        title = re.sub(r"\s+", "", document.title or "")
        if len(title) < 4:
            continue
        if document.document_number:
            question = f"{document.document_number}主要说明了什么事项？"
        elif document.document_role == "REPLY":
            question = f"《{title[:42]}》回复了哪些事项？"
        else:
            question = f"《{title[:42]}》的主要内容是什么？"
        if question not in {item["question"] for item in presets}:
            presets.append({"question": question, "document_id": document.id})
        if len(presets) >= 6:
            break
    return {
        "context": asdict(context),
        "cases": [{"value": key, "count": value} for key, value in cases.most_common()],
        "document_roles": [
            {"value": key, "count": value} for key, value in roles.most_common()
        ],
        "version_roles": [
            {"value": key, "count": value} for key, value in versions.most_common()
        ],
        "date_range": {
            "from": min(modified_values).date().isoformat() if modified_values else None,
            "to": max(modified_values).date().isoformat() if modified_values else None,
            "source": "SOURCE_FILE_MODIFIED_AT",
        },
        "presets": presets,
    }


def rerank_retrieval_results(
    config: RuntimeConfigBundleV1,
    query: str,
    retrieval: dict[str, Any],
) -> dict[str, Any]:
    """Rerank an already materialized retrieval response for an explicit workflow node."""
    results = list(retrieval.get("results") or [])
    if not config.routing.reranker_primary or not results:
        return retrieval
    documents = [
        "\n".join(
            value
            for value in [
                str(item.get("title") or ""),
                str(item.get("document_number") or ""),
                str(item.get("snippet") or "")[:4000],
            ]
            if value
        )
        for item in results
    ]
    try:
        reranked = rerank_documents(config, query, documents, len(documents))
    except CloudModelError as exc:
        return {
            **retrieval,
            "warnings": [
                *(retrieval.get("warnings") or []),
                f"云端重排序暂时不可用，已保留融合排序：{exc}",
            ],
            "cloud_usage": _merge_usage(retrieval.get("cloud_usage") or {}, exc.usage),
            "debug": {
                **(retrieval.get("debug") or {}),
                "reranker": {
                    "requested": True,
                    "configured": True,
                    "applied": False,
                    "warning": str(exc),
                },
            },
        }
    ordered: list[dict[str, Any]] = []
    selected_indexes: set[int] = set()
    for rank, item in enumerate(reranked.items, start=1):
        source = results[item.index]
        ordered.append(
            {
                **source,
                "rank": rank,
                "pre_rerank_score": source.get("score"),
                "score": item.score,
                "rerank_score": item.score,
                "ranking_algorithm": "QWEN_RERANK",
                "model_signature": reranked.model_signature,
            }
        )
        selected_indexes.add(item.index)
    for index, item in enumerate(results):
        if index not in selected_indexes:
            ordered.append({**item, "rank": len(ordered) + 1})
    return {
        **retrieval,
        "results": ordered,
        "total": len(ordered),
        "cloud_usage": _merge_usage(retrieval.get("cloud_usage") or {}, reranked.usage),
        "debug": {
            **(retrieval.get("debug") or {}),
            "reranker": {
                "requested": True,
                "configured": True,
                "applied": True,
                "candidate_count": len(documents),
                "model_signature": reranked.model_signature,
            },
        },
    }


def search(db: Session, request: RetrievalSearchRequest) -> dict[str, Any]:
    context = _resolve_context(db, request.index_generation_id)
    version = db.get(ConfigVersion, context.config_version_id)
    if not version:
        raise RetrievalContextError("索引关联的配置版本不存在")
    config = RuntimeConfigBundleV1.model_validate(version.content)
    runtime_version = get_current_config(db)
    runtime_config = RuntimeConfigBundleV1.model_validate(runtime_version.content)
    candidate_limit = min(50, max(request.limit * 3, 20))
    allowed_page_ids = _allowed_page_ids(db, context, request)
    visual_hits = []
    text_hits: list[TextSearchHit] = []
    semantic_hits: list[SemanticSearchHit] = []
    warnings: list[str] = []
    cloud_usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    if request.mode in {"visual", "hybrid"}:
        try:
            visual_hits = search_visual_pages(
                config,
                context.index_generation_id,
                request.query,
                candidate_limit if request.mode == "hybrid" or request.rerank else request.limit,
            )
            visual_hits = _filter_hits_by_page(visual_hits, allowed_page_ids)
        except Exception as exc:
            if request.mode == "visual":
                raise
            warnings.append(f"视觉检索暂时不可用，已使用文本检索：{exc}")
    if request.mode in {"text", "hybrid"}:
        text_hits = search_text_pages(
            db,
            context,
            request.query,
            candidate_limit if request.mode == "hybrid" or request.rerank else request.limit,
            allowed_page_ids,
        )
        if config.routing.text_embedding_primary:
            try:
                embedded = embed_query(config, request.query)
                cloud_usage = embedded.usage
                vector_hits = search_text_vectors(
                    config,
                    context.index_generation_id,
                    embedded.model_signature,
                    embedded.vector,
                    candidate_limit
                    if request.mode == "hybrid" or request.rerank
                    else request.limit,
                )
                best_pages: dict[str, SemanticSearchHit] = {}
                for hit in vector_hits:
                    chunk = db.get(Chunk, hit.chunk_id)
                    if chunk is None:
                        continue
                    semantic = SemanticSearchHit(
                        page_id=hit.page_id,
                        document_id=hit.document_id,
                        score=hit.score,
                        snippet=_query_centered_snippet(chunk.text, request.query),
                        model_signature=hit.model_signature,
                        collection=hit.collection,
                    )
                    previous = best_pages.get(hit.page_id)
                    if previous is None or semantic.score > previous.score:
                        best_pages[hit.page_id] = semantic
                semantic_hits = sorted(
                    best_pages.values(), key=lambda item: item.score, reverse=True
                )
                semantic_hits = _filter_hits_by_page(semantic_hits, allowed_page_ids)
            except Exception as exc:
                warnings.append(f"云端语义向量检索暂时不可用，已保留 BM25 结果：{exc}")

    if request.mode == "hybrid":
        hits = _fuse_hits(visual_hits, text_hits, semantic_hits)
    elif request.mode == "visual":
        hits = [
            FusedHit(
                page_id=hit.page_id,
                document_id=hit.document_id,
                score=hit.score,
                visual_score=hit.score,
                branch_ranks={"visual": rank},
            )
            for rank, hit in enumerate(visual_hits, start=1)
        ]
    else:
        if semantic_hits:
            hits = _fuse_hits([], text_hits, semantic_hits)
        else:
            hits = [
                FusedHit(
                    page_id=hit.page_id,
                    document_id=hit.document_id,
                    score=hit.score,
                    text_score=hit.score,
                    snippet=hit.snippet,
                    branch_ranks={"text": rank},
                )
                for rank, hit in enumerate(text_hits, start=1)
            ]

    reranker_debug: dict[str, Any] = {
        "requested": request.rerank,
        "configured": bool(runtime_config.routing.reranker_primary),
        "applied": False,
        "model_signature": None,
    }
    if request.rerank and runtime_config.routing.reranker_primary and hits:
        rerank_candidates = hits[:candidate_limit]
        documents: list[str] = []
        for hit in rerank_candidates:
            page = db.get(Page, hit.page_id)
            document = db.get(Document, hit.document_id)
            documents.append(
                "\n".join(
                    value
                    for value in [
                        document.title if document else "",
                        document.document_number or "" if document else "",
                        hit.snippet or (page.text[:4000] if page else ""),
                    ]
                    if value
                )
            )
        try:
            reranked = rerank_documents(
                runtime_config,
                request.query,
                documents,
                len(rerank_candidates),
            )
            ordered: list[FusedHit] = []
            selected_indexes: set[int] = set()
            for item in reranked.items:
                hit = rerank_candidates[item.index]
                hit.pre_rerank_score = hit.score
                hit.rerank_score = item.score
                hit.rerank_signature = reranked.model_signature
                hit.score = item.score
                ordered.append(hit)
                selected_indexes.add(item.index)
            ordered.extend(
                hit
                for index, hit in enumerate(rerank_candidates)
                if index not in selected_indexes
            )
            ordered.extend(hits[len(rerank_candidates) :])
            hits = ordered
            cloud_usage = _merge_usage(cloud_usage, reranked.usage)
            reranker_debug.update(
                {
                    "applied": True,
                    "model_signature": reranked.model_signature,
                    "candidate_count": len(rerank_candidates),
                }
            )
        except CloudModelError as exc:
            cloud_usage = _merge_usage(cloud_usage, exc.usage)
            warnings.append(f"云端重排序暂时不可用，已保留融合排序：{exc}")
            reranker_debug["warning"] = str(exc)

    hits = _diversify_hits(hits, request.limit)

    results: list[dict[str, Any]] = []
    for rank, hit in enumerate(hits, start=1):
        page = db.get(Page, hit.page_id)
        document = db.get(Document, hit.document_id)
        if page is None or document is None:
            continue
        source = db.get(SourceFile, document.source_file_id)
        results.append(
            {
                "rank": rank,
                "score": hit.score,
                "ranking_algorithm": (
                    "QWEN_RERANK"
                    if hit.rerank_score is not None
                    else "RRF"
                    if request.mode == "hybrid" or semantic_hits
                    else "MaxSim"
                    if request.mode == "visual"
                    else "BM25"
                ),
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
                "snippet": hit.snippet or page.text[:500],
                "preview_url": _artifact_url(page.image_path),
                "model_signature": (
                    hit.rerank_signature
                    if hit.rerank_score is not None
                    else "fusion:rrf-v2"
                    if len(hit.match_sources) > 1
                    else hit.semantic_signature
                    if hit.semantic_score is not None
                    else "local:bm25-char-bigram-v1"
                    if hit.text_score is not None
                    else next(
                        (
                            item.model_signature
                            for item in visual_hits
                            if item.page_id == hit.page_id
                        ),
                        "colpali",
                    )
                ),
                "collection": (
                    "hybrid"
                    if len(hit.match_sources) > 1
                    else hit.semantic_collection
                    if hit.semantic_score is not None
                    else "postgresql:chunks"
                    if hit.text_score is not None
                    else next(
                        (item.collection for item in visual_hits if item.page_id == hit.page_id),
                        "visual",
                    )
                ),
                "match_sources": hit.match_sources,
                "visual_score": hit.visual_score,
                "text_score": hit.text_score,
                "semantic_score": hit.semantic_score,
                "branch_ranks": hit.branch_ranks or {},
                "rrf_contributions": hit.rrf_contributions or {},
                "rrf_score": hit.pre_rerank_score if hit.pre_rerank_score is not None else (
                    hit.score if request.mode == "hybrid" or semantic_hits else None
                ),
                "rerank_score": hit.rerank_score,
            }
        )
    return {
        "query": request.query,
        "mode": request.mode,
        "context": asdict(context),
        "runtime_config_version_id": runtime_version.id,
        "total": len(results),
        "warnings": warnings,
        "cloud_usage": cloud_usage,
        "results": results,
        "debug": {
            "candidate_limit": candidate_limit,
            "filters": {
                "case_ids": request.case_ids,
                "document_roles": request.document_roles,
                "version_roles": request.version_roles,
                "date_from": request.date_from.isoformat() if request.date_from else None,
                "date_to": request.date_to.isoformat() if request.date_to else None,
                "min_authority_score": request.min_authority_score,
                "authoritative_only": request.authoritative_only,
                "matched_pages": len(allowed_page_ids) if allowed_page_ids is not None else None,
            },
            "branches": {
                "visual": {
                    "total": len(visual_hits),
                    "results": _branch_debug_rows(db, visual_hits),
                },
                "bm25": {
                    "total": len(text_hits),
                    "model_signature": "local:bm25-char-bigram-v1",
                    "collection": "postgresql:chunks",
                    "results": _branch_debug_rows(db, text_hits),
                },
                "semantic": {
                    "total": len(semantic_hits),
                    "model_signature": semantic_hits[0].model_signature if semantic_hits else None,
                    "collection": semantic_hits[0].collection if semantic_hits else None,
                    "results": _branch_debug_rows(db, semantic_hits),
                },
            },
            "fusion": {"algorithm": "RRF", "constant": 60},
            "reranker": reranker_debug,
        } if request.debug else None,
    }


def answer(db: Session, request: RetrievalAnswerRequest) -> dict[str, Any]:
    from docflow.workflows.qa import run_qa_workflow

    return run_qa_workflow(db, request)
