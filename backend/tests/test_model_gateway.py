from __future__ import annotations

import json

from docflow.domain.config import default_runtime_config
from docflow.services.model_gateway import embed_query, generate_chat_answer


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.body


class FakeClient:
    response_body: dict = {}
    last_url = ""
    last_payload: dict = {}

    def __init__(self, **_) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        assert headers["Authorization"].startswith("Bearer ")
        self.__class__.last_url = url
        self.__class__.last_payload = json
        return FakeResponse(self.response_body)


def _enable(config, profile_id: str, route_name: str) -> None:
    profile = next(item for item in config.models if item.profile_id == profile_id)
    profile.enabled = True
    setattr(config.routing, route_name, profile_id)


_RESULTS = [
    {
        "page_id": "page_1",
        "document_id": "doc_1",
        "case_id": "case_1",
        "title": "停车场经营管理事项复函",
        "document_number": "示例办复〔2026〕5号",
        "page_number": 1,
        "relative_path": "复函.pdf",
        "snippet": "实施停车场升级改造工程，预算费用约5万元。",
        "preview_url": "/artifacts/page.png",
        "match_sources": ["semantic", "text"],
    }
]


def test_bailian_query_embedding_uses_cloud_dimension(monkeypatch) -> None:
    config = default_runtime_config()
    _enable(config, "bailian_embedding", "text_embedding_primary")
    next(item for item in config.models if item.profile_id == "bailian_embedding").base_url = (
        "https://ws-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-dashscope-key")
    FakeClient.response_body = {
        "data": [{"index": 0, "embedding": [0.25] * 2560}],
        "usage": {"prompt_tokens": 12, "total_tokens": 12},
    }
    monkeypatch.setattr("docflow.services.model_gateway.httpx.Client", FakeClient)

    result = embed_query(config, "公文检索问题")

    assert len(result.vector) == 2560
    assert result.model_signature == "dashscope:qwen3.7-text-embedding:2560"
    assert result.usage == {"calls": 1, "input_tokens": 12, "output_tokens": 0}
    assert FakeClient.last_url.endswith("/embeddings")
    assert FakeClient.last_payload["dimensions"] == 2560


def test_openai_compatible_answer_normalizes_declared_citations_and_confidence(monkeypatch) -> None:
    config = default_runtime_config()
    _enable(config, "cloud_chat_llm", "qa_generation_primary")
    monkeypatch.setenv("CHAT_LLM_API_KEY", "test-chat-key")
    FakeClient.response_body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "answer": "停车场升级改造预算约为5万元。",
                            "citation_ids": [1],
                            "confidence": "high",
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 18},
    }
    monkeypatch.setattr("docflow.services.model_gateway.httpx.Client", FakeClient)

    result = generate_chat_answer(config, "停车场升级改造预算是多少", _RESULTS, 4)

    assert result.answer.endswith("[1]")
    assert result.citations[0]["page_id"] == "page_1"
    assert result.confidence == 0.9
    assert result.model_signature == "siliconflow:deepseek-ai/DeepSeek-V4-Flash"
    assert result.usage == {
        "calls": 1,
        "input_tokens": 120,
        "output_tokens": 18,
        "estimated_cost_cny": 0.0,
    }
    assert FakeClient.last_url == "https://api.siliconflow.cn/v1/chat/completions"
    assert FakeClient.last_payload["model"] == "deepseek-ai/DeepSeek-V4-Flash"
    assert FakeClient.last_payload["enable_thinking"] is False


def test_chat_gateway_follows_profile_instead_of_hardcoded_provider(monkeypatch) -> None:
    config = default_runtime_config()
    _enable(config, "cloud_chat_llm", "qa_generation_primary")
    profile = next(item for item in config.models if item.profile_id == "cloud_chat_llm")
    profile.base_url = "https://other-vendor.example.com/openai/v1/"
    profile.model_name = "vendor-chat-32k"
    profile.secret_env_name = "OTHER_VENDOR_KEY"
    profile.request_options = {"reasoning_effort": "low"}
    profile.price_input_per_million = 2.0
    profile.price_output_per_million = 8.0
    monkeypatch.setenv("OTHER_VENDOR_KEY", "test-other-key")
    FakeClient.response_body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"answer": "预算约5万元。[1]", "citation_ids": [1], "confidence": 0.8},
                        ensure_ascii=False,
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
    }
    monkeypatch.setattr("docflow.services.model_gateway.httpx.Client", FakeClient)

    result = generate_chat_answer(config, "停车场升级改造预算是多少", _RESULTS, 4)

    assert FakeClient.last_url == "https://other-vendor.example.com/openai/v1/chat/completions"
    assert FakeClient.last_payload["model"] == "vendor-chat-32k"
    assert FakeClient.last_payload["reasoning_effort"] == "low"
    assert "enable_thinking" not in FakeClient.last_payload
    assert result.usage["estimated_cost_cny"] == 10.0
