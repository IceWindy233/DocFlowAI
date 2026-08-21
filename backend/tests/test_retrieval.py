import threading
import time
from types import SimpleNamespace

from sqlalchemy.orm import Session

from docflow.db.models import (
    Chunk,
    Document,
    IngestionJob,
    Page,
    Publication,
    SourceFile,
    WorkflowRun,
)
from docflow.domain.config import default_runtime_config
from docflow.domain.retrieval import RetrievalAnswerRequest
from docflow.services.config_service import ensure_default_config
from docflow.services.retrieval import (
    FusedHit,
    RetrievalContext,
    _diversify_hits,
    _fuse_hits,
    _query_centered_snippet,
    _resolve_context,
    generate_extractive_answer,
    search,
    search_text_pages,
)
from docflow.services.vector_index import (
    _ENCODERS,
    _get_encoder,
    delete_visual_document_points,
    search_visual_pages,
    visual_collection_name,
)
from docflow.workflows.qa import (
    _append_adjacent_page_candidates,
    _prioritize_organization_evidence,
    _promote_adjacent_candidate_page,
    classify_intent,
    run_qa_workflow,
)


def test_multi_fact_question_promotes_retrieved_adjacent_page() -> None:
    results = [
        {"page_id": "p1", "document_id": "doc_a", "page_number": 1},
        {"page_id": "p9", "document_id": "doc_b", "page_number": 9},
        {"page_id": "p8", "document_id": "doc_c", "page_number": 8},
        {"page_id": "p7", "document_id": "doc_d", "page_number": 7},
        {"page_id": "p6", "document_id": "doc_e", "page_number": 6},
        {"page_id": "p2", "document_id": "doc_a", "page_number": 2},
    ]
    ordered, promoted = _promote_adjacent_candidate_page(
        "共有多少个停车位，使用费多少，借款金额多少？",
        results,
        evidence_limit=5,
    )
    assert [item["page_id"] for item in ordered[:2]] == ["p1", "p2"]
    assert promoted == ["p2"]


def test_single_fact_question_keeps_reranker_order() -> None:
    results = [
        {"page_id": "p1", "document_id": "doc_a", "page_number": 1},
        {"page_id": "p2", "document_id": "doc_a", "page_number": 2},
    ]
    ordered, promoted = _promote_adjacent_candidate_page(
        "项目预算是多少？",
        results,
        evidence_limit=1,
    )
    assert ordered == results
    assert promoted == []


def test_multi_fact_question_materializes_missing_adjacent_page(db: Session) -> None:
    config_version = ensure_default_config(db)
    job = IngestionJob(
        job_type="FULL_SCAN",
        source_root="/tmp/source",
        config_version_id=config_version.id,
        index_generation_id="idx_adjacent",
    )
    db.add(job)
    db.flush()
    source = SourceFile(
        job_id=job.id,
        source_path="/tmp/source/multi.pdf",
        relative_path="multi.pdf",
        file_name="multi.pdf",
    )
    db.add(source)
    db.flush()
    document = Document(
        source_file_id=source.id,
        config_version_id=config_version.id,
        case_id="case_multi",
        title="跨页请示",
        parser_route="PDF_NATIVE",
        parser_version="test",
    )
    db.add(document)
    db.flush()
    first = Page(
        id=f"{document.id}_p0001",
        document_id=document.id,
        page_number=1,
        text="停车位3639个，使用费5921.90万元。",
    )
    second = Page(
        id=f"{document.id}_p0002",
        document_id=document.id,
        page_number=2,
        text="申请银行借款6200万元。",
    )
    db.add_all([first, second])
    db.commit()

    expanded = _append_adjacent_page_candidates(
        db,
        "共有多少个停车位，使用费多少，借款金额多少？",
        [
            {
                "page_id": first.id,
                "document_id": document.id,
                "page_number": 1,
            }
        ],
        evidence_limit=5,
    )
    assert [item["page_id"] for item in expanded] == [first.id, second.id]
    assert "6200万元" in expanded[1]["snippet"]
    assert expanded[1]["ranking_algorithm"] == "ADJACENT_CONTEXT"


class FakeEncoder:
    def encode_query(self, query: str) -> list[list[float]]:
        assert query == "年度预算表"
        return [[0.1, 0.2], [0.3, 0.4]]


def test_visual_search_uses_colpali_query_vectors(monkeypatch) -> None:
    config = default_runtime_config()
    signature = "vidore/colqwen2.5-v0.2"
    collection = visual_collection_name(config, "idx_demo", signature)

    class FakeQdrant:
        def get_collections(self):
            return SimpleNamespace(collections=[SimpleNamespace(name=collection)])

        def query_points(self, **kwargs):
            assert kwargs["collection_name"] == collection
            assert kwargs["query"] == [[0.1, 0.2], [0.3, 0.4]]
            return SimpleNamespace(
                points=[
                    SimpleNamespace(
                        score=4.2,
                        payload={"page_id": "page_1", "document_id": "doc_1"},
                    )
                ]
            )

    monkeypatch.setattr("docflow.services.vector_index.qdrant_client", lambda: FakeQdrant())
    monkeypatch.setitem(
        __import__("docflow.services.vector_index", fromlist=["_ENCODERS"])._ENCODERS,
        signature,
        FakeEncoder(),
    )

    hits = search_visual_pages(config, "idx_demo", "年度预算表", limit=5)
    assert len(hits) == 1
    assert hits[0].page_id == "page_1"
    assert hits[0].score == 4.2


def test_colpali_encoder_is_loaded_once_across_concurrent_requests(monkeypatch) -> None:
    signature = "test/concurrent-model"
    _ENCODERS.pop(signature, None)
    created: list[str] = []

    class FakeConcurrentEncoder:
        def __init__(self, model_name: str) -> None:
            time.sleep(0.03)
            created.append(model_name)

    monkeypatch.setattr(
        "docflow.services.vector_index.ColPaliEncoder",
        FakeConcurrentEncoder,
    )
    results: list[object] = []

    def load() -> None:
        results.append(_get_encoder(signature, signature))

    threads = [threading.Thread(target=load) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert created == [signature]
    assert len({id(item) for item in results}) == 1
    _ENCODERS.pop(signature, None)


def test_retrieval_context_prefers_active_publication(db: Session) -> None:
    config_version = ensure_default_config(db)
    job = IngestionJob(
        job_type="FULL_SCAN",
        source_root="/tmp/source",
        config_version_id=config_version.id,
        index_generation_id="idx_latest",
    )
    db.add(job)
    db.flush()
    source = SourceFile(
        job_id=job.id,
        source_path="/tmp/source/a.pdf",
        relative_path="a.pdf",
        file_name="a.pdf",
    )
    db.add(source)
    db.flush()
    db.add(
        Document(
            source_file_id=source.id,
            config_version_id=config_version.id,
            case_id="case_1",
            title="测试文档",
            parser_route="PDF_NATIVE",
            parser_version="test",
        )
    )
    db.add(
        Publication(
            config_version_id=config_version.id,
            index_generation_id="idx_published",
            status="PUBLISHED",
            active=True,
        )
    )
    db.commit()

    context = _resolve_context(db)
    assert context.index_generation_id == "idx_published"
    assert context.source == "ACTIVE_PUBLICATION"


def test_visual_cleanup_deletes_document_points_from_all_model_collections(
    monkeypatch,
) -> None:
    config = default_runtime_config()
    collections = {
        visual_collection_name(config, "idx_demo", "vidore/colqwen2.5-v0.2"),
        visual_collection_name(config, "idx_demo", "vidore/colqwen2-v1.0"),
    }
    deleted: list[str] = []

    class FakeQdrant:
        def get_collections(self):
            return SimpleNamespace(
                collections=[SimpleNamespace(name=name) for name in collections]
            )

        def delete(self, collection_name, points_selector, wait):
            assert points_selector.filter.must[0].match.value == "doc_failed"
            assert wait is True
            deleted.append(collection_name)

    monkeypatch.setattr("docflow.services.vector_index.qdrant_client", lambda: FakeQdrant())
    delete_visual_document_points(config, "idx_demo", "doc_failed")
    assert set(deleted) == collections


def test_chinese_text_search_returns_matching_page(db: Session) -> None:
    config_version = ensure_default_config(db)
    job = IngestionJob(
        job_type="FULL_SCAN",
        source_root="/tmp/source",
        config_version_id=config_version.id,
        index_generation_id="idx_text",
    )
    db.add(job)
    db.flush()
    source = SourceFile(
        job_id=job.id,
        source_path="/tmp/source/reply.pdf",
        relative_path="reply.pdf",
        file_name="reply.pdf",
    )
    db.add(source)
    db.flush()
    document = Document(
        source_file_id=source.id,
        config_version_id=config_version.id,
        case_id="case_parking",
        title="关于停车场收费事项的复函",
        parser_route="PDF_NATIVE",
        parser_version="test",
    )
    db.add(document)
    db.flush()
    page = Page(
        id=f"{document.id}_p0001",
        document_id=document.id,
        page_number=1,
        text="停车场运营预算为五万元，年度场地租金为六万元。",
    )
    db.add(page)
    db.flush()
    db.add(
        Chunk(
            id=f"{document.id}_c00001",
            document_id=document.id,
            page_id=page.id,
            ordinal=0,
            text=page.text,
        )
    )
    db.commit()

    hits = search_text_pages(
        db,
        RetrievalContext(config_version.id, job.index_generation_id, "TEST"),
        "停车场运营预算是多少",
        5,
    )
    assert hits[0].page_id == page.id
    assert hits[0].score > 0


def test_hybrid_fusion_rewards_pages_found_by_both_routes() -> None:
    visual = [
        SimpleNamespace(page_id="visual_only", document_id="doc_1", score=9.0),
        SimpleNamespace(page_id="both", document_id="doc_2", score=8.0),
    ]
    text = [
        SimpleNamespace(page_id="text_only", document_id="doc_3", score=7.0, snippet="text"),
        SimpleNamespace(page_id="both", document_id="doc_2", score=6.0, snippet="both"),
    ]
    hits = _fuse_hits(visual, text)
    assert hits[0].page_id == "both"
    assert hits[0].match_sources == ["visual", "text"]


def test_cloud_text_embedding_is_used_for_online_semantic_retrieval(
    db: Session,
    monkeypatch,
) -> None:
    config_version = ensure_default_config(db)
    config = default_runtime_config()
    embedding = next(item for item in config.models if item.profile_id == "bailian_embedding")
    embedding.enabled = True
    embedding.workspace_id = "llm-test-workspace"
    embedding.base_url = (
        "https://llm-test-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    config.routing.text_embedding_primary = embedding.profile_id
    config_version.content = config.model_dump(mode="json")
    config_version.content_hash = config.content_hash()
    job = IngestionJob(
        job_type="FULL_SCAN",
        source_root="/tmp/source",
        config_version_id=config_version.id,
        index_generation_id="idx_semantic",
    )
    db.add(job)
    db.flush()
    source = SourceFile(
        job_id=job.id,
        source_path="/tmp/source/semantic.pdf",
        relative_path="semantic.pdf",
        file_name="semantic.pdf",
    )
    db.add(source)
    db.flush()
    document = Document(
        source_file_id=source.id,
        config_version_id=config_version.id,
        case_id="case_semantic",
        title="国有物业盘活方案",
        parser_route="PDF_NATIVE",
        parser_version="test",
    )
    db.add(document)
    db.flush()
    page = Page(
        id=f"{document.id}_p0001",
        document_id=document.id,
        page_number=1,
        text="提高国有资产使用效率。",
    )
    db.add(page)
    db.flush()
    chunk = Chunk(
        id=f"{document.id}_c00001",
        document_id=document.id,
        page_id=page.id,
        ordinal=0,
        text=page.text,
        embedding_status="READY",
        embedding_signature=embedding.model_signature,
    )
    db.add(chunk)
    db.add(
        Publication(
            config_version_id=config_version.id,
            index_generation_id=job.index_generation_id,
            status="PUBLISHED",
            active=True,
        )
    )
    db.commit()
    monkeypatch.setattr(
        "docflow.services.retrieval.embed_query",
        lambda *_: SimpleNamespace(
            vector=[0.1] * 2560,
            model_signature=embedding.model_signature,
            usage={"calls": 1, "input_tokens": 7, "output_tokens": 0},
        ),
    )
    monkeypatch.setattr(
        "docflow.services.retrieval.search_text_vectors",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                chunk_id=chunk.id,
                page_id=page.id,
                document_id=document.id,
                score=0.91,
                model_signature=embedding.model_signature,
                collection="docflow_text_test",
            )
        ],
    )

    response = search(
        db,
        RetrievalAnswerRequest(query="闲置资源如何利用", mode="text", limit=5),
    )

    assert response["results"][0]["page_id"] == page.id
    assert "semantic" in response["results"][0]["match_sources"]
    assert response["results"][0]["semantic_score"] == 0.91
    assert response["cloud_usage"]["input_tokens"] == 7


def test_retrieval_diversity_prefers_distinct_documents() -> None:
    hits = [
        FusedHit("page_1", "doc_a", 1.0),
        FusedHit("page_2", "doc_a", 0.9),
        FusedHit("page_3", "doc_b", 0.8),
        FusedHit("page_4", "doc_c", 0.7),
    ]
    selected = _diversify_hits(hits, 3)
    assert [item.page_id for item in selected] == ["page_1", "page_2", "page_3"]


def test_query_intent_classification() -> None:
    assert classify_intent("停车场升级改造预算是多少") == "NUMERIC_FACT"
    assert classify_intent("营业执照法定代表人是谁") == "PERSON_FACT"
    assert classify_intent("有哪些单位回复意见") == "ORGANIZATION_AGGREGATION"


def test_organization_aggregation_prioritizes_explicit_direct_replies() -> None:
    results = [
        {"document_id": "request", "document_role": "REQUEST", "snippet": "汇总表无意见"},
        {"document_id": "reply_a", "document_role": "REPLY", "snippet": "经研究，我单位无意见。"},
        {"document_id": "reply_pending", "document_role": "REPLY", "snippet": "提出修改意见。"},
        {"document_id": "reply_b", "document_role": "REPLY", "snippet": "我中心无不同意见。"},
    ]

    prioritized = _prioritize_organization_evidence(results)

    assert [item["document_id"] for item in prioritized] == [
        "reply_a",
        "reply_b",
        "request",
        "reply_pending",
    ]


def test_langgraph_qa_workflow_persists_trace(db: Session, monkeypatch) -> None:
    retrieval = {
        "query": "停车场升级改造预算是多少",
        "mode": "text",
        "context": {
            "config_version_id": "cfg_demo",
            "index_generation_id": "idx_demo",
            "source": "TEST",
        },
        "total": 1,
        "warnings": [],
        "results": [
            {
                "rank": 1,
                "score": 12.0,
                "page_id": "page_1",
                "page_number": 1,
                "page_type": "TEXT",
                "document_id": "doc_1",
                "title": "停车场经营事项复函",
                "document_number": "示例办复〔2026〕5号",
                "case_id": "case_1",
                "document_role": "REPLY",
                "version_role": "REPLY",
                "authority_score": 0.9,
                "relative_path": "复函.pdf",
                "snippet": "停车场升级改造工程，预算费用约5万元。",
                "preview_url": "/artifacts/page.png",
                "model_signature": "test",
                "collection": "test",
                "match_sources": ["text"],
                "visual_score": None,
                "text_score": 12.0,
            }
        ],
    }
    monkeypatch.setattr("docflow.workflows.qa.search", lambda *_: retrieval)

    response = run_qa_workflow(
        db,
        RetrievalAnswerRequest(
            query="停车场升级改造预算是多少",
            mode="text",
            limit=5,
        ),
    )

    assert "5万元" in response["answer"]
    assert response["verification"]["citations_valid"] is True
    assert [step["node"] for step in response["workflow"]["trace"]] == [
        "understand_query",
        "rewrite_query",
        "retrieve_evidence",
        "rerank_evidence",
        "assess_evidence",
        "generate_answer",
        "verify_citations",
    ]
    run = db.get(WorkflowRun, response["workflow"]["run_id"])
    assert run is not None
    assert run.status == "SUCCEEDED"
    assert run.index_generation_id == "idx_demo"


def test_extractive_answer_preserves_numeric_evidence_and_citation() -> None:
    retrieval = {
        "results": [
            {
                "rank": 1,
                "page_id": "page_parking",
                "document_id": "doc_parking",
                "case_id": "case_parking",
                "title": "关于停车场经营管理事项的复函",
                "document_number": "示例办复〔2026〕5号",
                "document_role": "REPLY",
                "page_number": 1,
                "relative_path": "停车场/复函.pdf",
                "snippet": (
                    "实施停车场升级改造工程，预算费用约5万元。"
                    "购买保安服务预算费用约60000元/年。"
                ),
                "preview_url": "/artifacts/page.png",
                "match_sources": ["visual", "text"],
            }
        ]
    }
    response = generate_extractive_answer("停车场升级改造预算是多少", retrieval)
    assert "5万元" in response["answer"]
    assert "[1]" in response["answer"]
    assert response["citations"][0]["page_id"] == "page_parking"
    assert response["confidence"] >= 0.6


def test_query_centered_snippet_selects_matching_table_row() -> None:
    value = "\n".join(
        [
            "表头：公司名称；变更前；变更后；新增范围",
            *(f"第{index}行：其他公司；一般经营项目" for index in range(30)),
            "某市示例商务服务有限公司；变更后新增建筑材料销售、塑料制品销售",
            *(f"后续第{index}行：其他公司；一般经营项目" for index in range(30)),
        ]
    )
    snippet = _query_centered_snippet(
        value, "某市示例商务服务有限公司新增哪些经营范围", maximum=300
    )
    assert "某市示例商务服务有限公司" in snippet
    assert "塑料制品销售" in snippet


def test_extractive_answer_pairs_company_with_legal_representative() -> None:
    retrieval = {
        "results": [
            {
                "rank": 1,
                "page_id": "page_license",
                "document_id": "doc_license",
                "case_id": "case_license",
                "title": "营业执照",
                "document_number": None,
                "document_role": "ATTACHMENT",
                "page_number": 1,
                "relative_path": "附件/营业执照.pdf",
                "snippet": (
                    "名称\n某市示例商务服务有限公司\n注册资本1000万元\n"
                    "法定代表人测试人员甲\n"
                ),
                "preview_url": "/artifacts/license.png",
                "match_sources": ["visual", "text"],
            }
        ]
    }

    response = generate_extractive_answer("营业执照法定代表人是谁", retrieval)
    assert "某市示例商务服务有限公司" in response["answer"]
    assert "测试人员甲" in response["answer"]
    assert response["citations"][0]["case_id"] == "case_license"


def test_extractive_answer_aggregates_reply_organizations() -> None:
    retrieval = {
        "results": [
            {
                "rank": 1,
                "page_id": "page_finance",
                "document_id": "doc_finance",
                "case_id": "case_market",
                "title": "关于报送回复意见的函",
                "document_number": "示例财复〔2027〕12号",
                "document_role": "REPLY",
                "page_number": 1,
                "relative_path": "财政复函.pdf",
                "snippet": "某市财政局示例分局\n示例财复〔2027〕12号\n我单位无意见。",
                "preview_url": None,
                "match_sources": ["visual", "text"],
            },
            {
                "rank": 2,
                "page_id": "page_justice",
                "document_id": "doc_justice",
                "case_id": "case_market",
                "title": "关于征求意见函的复函",
                "document_number": "示例司函〔2027〕8号",
                "document_role": "REPLY",
                "page_number": 1,
                "relative_path": "司法复函.pdf",
                "snippet": "某市司法局示例分局\n示例司函〔2027〕8号\n我单位无\n意见。",
                "preview_url": None,
                "match_sources": ["visual", "text"],
            },
            {
                "rank": 3,
                "page_id": "page_planning",
                "document_id": "doc_planning",
                "case_id": "case_market",
                "title": "关于征求意见函的复函",
                "document_number": "示例规复〔2027〕5号",
                "document_role": "REPLY",
                "page_number": 1,
                "relative_path": "规划复函.pdf",
                "snippet": "某市示例规划管理所\n（城市更新中心）\n我单位无意见。",
                "preview_url": None,
                "match_sources": ["visual", "text"],
            },
        ]
    }
    response = generate_extractive_answer("哪些单位进行了回复", retrieval)
    assert "某市财政局示例分局" in response["answer"]
    assert "某市司法局示例分局" in response["answer"]
    assert "某市示例规划管理所（城市更新中心）" in response["answer"]
    assert len(response["citations"]) == 3


def test_extractive_answer_refuses_when_no_evidence() -> None:
    response = generate_extractive_answer("不存在的问题", {"results": []})
    assert "无法回答" in response["answer"]
    assert response["citations"] == []
    assert response["confidence"] == 0.0
