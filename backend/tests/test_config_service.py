from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy.orm import Session

from docflow.domain.config import (
    AdapterType,
    ChangeImpact,
    ModelProfileV1,
    RuntimeConfigBundleV1,
    dashscope_workspace_base_url,
    default_runtime_config,
)
from docflow.domain.jobs import IngestionJobCreate
from docflow.services.config_service import (
    ConfigConflictError,
    ModelNotReadyError,
    calculate_impact,
    create_rebuild_job,
    ensure_default_config,
    list_versions,
    model_profile_probe_fingerprint,
    probe_model,
    rollback_config,
    save_config,
    secret_status,
)
from docflow.services.jobs import create_job


class ProbeResponse:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class ProbeClient:
    response_body: dict = {}
    last_url = ""
    last_payload: dict = {}

    def __init__(self, **_) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def post(self, url: str, *, headers: dict, json: dict) -> ProbeResponse:
        assert headers["Authorization"].startswith("Bearer ")
        self.__class__.last_url = url
        self.__class__.last_payload = json
        return ProbeResponse(self.response_body)


def config_from(version) -> RuntimeConfigBundleV1:
    return RuntimeConfigBundleV1.model_validate(deepcopy(version.content))


def test_default_config_is_bootstrapped_once(db: Session) -> None:
    first = ensure_default_config(db)
    second = ensure_default_config(db)
    assert first.id == second.id
    assert first.active is True
    assert first.content_hash == config_from(first).content_hash()


def test_impact_classification(db: Session) -> None:
    current = ensure_default_config(db)
    base = config_from(current)

    hot = base.model_copy(deep=True)
    hot.budget.estimated_cost_cny_limit += 10
    assert calculate_impact(base, hot).impact == ChangeImpact.HOT

    reparse = base.model_copy(deep=True)
    reparse.parsing.pdf_render_dpi = 300
    assert calculate_impact(base, reparse).impact == ChangeImpact.REPARSE_REQUIRED

    reindex = base.model_copy(deep=True)
    reindex.chunking.target_max_chars += 100
    assert calculate_impact(base, reindex).impact == ChangeImpact.REINDEX_REQUIRED


def test_save_is_immutable_and_uses_optimistic_lock(db: Session) -> None:
    current = ensure_default_config(db)
    changed = config_from(current)
    changed.budget.max_cloud_calls_per_job = 25
    saved = save_config(
        db,
        base_version_id=current.id,
        config=changed,
        change_reason="调整调用上限",
    )
    assert saved.version == 2
    assert saved.active is True
    assert current.active is False
    assert len(list_versions(db)) == 2

    with pytest.raises(ConfigConflictError):
        save_config(
            db,
            base_version_id=current.id,
            config=changed,
            change_reason="过期页面覆盖",
        )


def test_rollback_creates_new_version(db: Session) -> None:
    initial = ensure_default_config(db)
    changed = config_from(initial)
    changed.execution.cpu_worker_concurrency = 8
    saved = save_config(
        db,
        base_version_id=initial.id,
        config=changed,
        change_reason="提高并发",
    )
    rollback = rollback_config(db, initial.id)
    assert rollback.version == 3
    assert rollback.parent_id == saved.id
    assert rollback.content_hash == initial.content_hash


def test_secret_status_never_returns_value(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    version = ensure_default_config(db)
    config = config_from(version)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "super-secret-value")
    status = secret_status(config, "bailian_vlm")
    assert status == {
        "profile_id": "bailian_vlm",
        "required": True,
        "configured": True,
        "env_name": "DASHSCOPE_API_KEY",
    }
    assert "super-secret-value" not in str(status)


def test_default_config_separates_bailian_embedding_and_generic_chat_routes(
    db: Session,
) -> None:
    config = config_from(ensure_default_config(db))
    embedding = next(item for item in config.models if item.profile_id == "bailian_embedding")
    generation = next(item for item in config.models if item.profile_id == "cloud_chat_llm")

    assert embedding.provider_id == "dashscope"
    assert embedding.embedding_dimension == 2560
    assert generation.adapter_type == AdapterType.OPENAI_COMPATIBLE
    assert generation.secret_env_name == "CHAT_LLM_API_KEY"
    assert generation.request_options == {"enable_thinking": False}
    assert config.routing.text_embedding_primary is None
    assert config.routing.qa_generation_primary is None
    assert "DASHSCOPE_API_KEY" in config.security.allowed_secret_env_names
    assert "CHAT_LLM_API_KEY" in config.security.allowed_secret_env_names


def test_adapter_types_expose_no_vendor_specific_chat_protocol() -> None:
    assert {item.value for item in AdapterType} == {
        "dashscope_openai",
        "openai_compatible",
        "local_transformers",
        "rapidocr",
        "tesseract",
        "docling",
        "libreoffice",
    }


def test_openai_compatible_request_options_cannot_override_core_payload() -> None:
    profile = next(
        item for item in default_runtime_config().models if item.profile_id == "cloud_chat_llm"
    )
    payload = profile.model_dump(mode="python")
    payload["request_options"] = {"model": "unexpected-model"}

    with pytest.raises(ValueError, match="不能覆盖受保护字段"):
        ModelProfileV1.model_validate(payload)


def test_bailian_workspace_id_is_the_endpoint_source_of_truth(db: Session) -> None:
    config = config_from(ensure_default_config(db))
    raw = next(item for item in config.models if item.profile_id == "bailian_embedding")
    payload = raw.model_dump(mode="json")
    payload["workspace_id"] = "llm-workspace-test"
    payload["base_url"] = "https://incorrect.example.com/v1"

    profile = ModelProfileV1.model_validate(payload)

    assert profile.workspace_id == "llm-workspace-test"
    assert profile.base_url == dashscope_workspace_base_url("llm-workspace-test")


def test_legacy_workspace_url_is_migrated_to_explicit_snapshot_field(db: Session) -> None:
    current = ensure_default_config(db)
    content = deepcopy(current.content)
    embedding = next(
        item for item in content["models"] if item["profile_id"] == "bailian_embedding"
    )
    embedding.pop("workspace_id", None)
    embedding["base_url"] = dashscope_workspace_base_url("llm-legacy-workspace")
    current.content = content
    current.content_hash = "legacy-without-workspace-field"
    db.commit()

    upgraded = ensure_default_config(db)
    upgraded_profile = next(
        item
        for item in upgraded.content["models"]
        if item["profile_id"] == "bailian_embedding"
    )

    assert upgraded.version == 2
    assert upgraded_profile["workspace_id"] == "llm-legacy-workspace"
    assert upgraded.impact == ChangeImpact.HOT.value


def test_workspace_change_is_hot_but_requires_a_new_probe(db: Session) -> None:
    base = config_from(ensure_default_config(db))
    changed = base.model_copy(deep=True)
    before = next(item for item in base.models if item.profile_id == "bailian_embedding")
    after = next(item for item in changed.models if item.profile_id == "bailian_embedding")
    after.workspace_id = "llm-another-workspace"
    after.base_url = dashscope_workspace_base_url(after.workspace_id)

    impact = calculate_impact(base, changed)

    assert impact.impact == ChangeImpact.HOT
    assert model_profile_probe_fingerprint(before) != model_profile_probe_fingerprint(after)


def test_cloud_routes_require_environment_and_successful_probe(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHAT_LLM_API_KEY", raising=False)
    current = ensure_default_config(db)
    config = config_from(current)
    generation = next(item for item in config.models if item.profile_id == "cloud_chat_llm")
    generation.enabled = True
    config.routing.qa_generation_primary = generation.profile_id

    with pytest.raises(ModelNotReadyError, match="CHAT_LLM_API_KEY 未配置"):
        save_config(
            db,
            base_version_id=current.id,
            config=config,
            change_reason="启用云端对话模型问答",
        )


def test_enabling_bailian_embedding_requires_new_index_generation(db: Session) -> None:
    current = ensure_default_config(db)
    base = config_from(current)
    changed = base.model_copy(deep=True)
    embedding = next(
        item for item in changed.models if item.profile_id == "bailian_embedding"
    )
    embedding.enabled = True
    changed.routing.text_embedding_primary = embedding.profile_id

    impact = calculate_impact(base, changed)
    assert impact.impact == ChangeImpact.REINDEX_REQUIRED
    assert impact.requires_rebuild is True


def test_jobs_pin_configuration_version(db: Session, tmp_path) -> None:
    initial = ensure_default_config(db)
    first_job = create_job(db, IngestionJobCreate(source_root=str(tmp_path)))
    changed = config_from(initial)
    changed.budget.max_cloud_calls_per_job += 1
    active = save_config(
        db,
        base_version_id=initial.id,
        config=changed,
        change_reason="测试任务配置固定",
    )
    second_job = create_job(db, IngestionJobCreate(source_root=str(tmp_path)))
    assert first_job.config_version_id == initial.id
    assert second_job.config_version_id == active.id


def test_rebuild_job_carries_cloud_authorization_from_config(db: Session) -> None:
    current = ensure_default_config(db)
    config = config_from(current)
    config.budget.cloud_processing_allowed = True
    active = save_config(
        db,
        base_version_id=current.id,
        config=config,
        change_reason="允许云端影子重建",
    )

    job = create_rebuild_job(db)

    assert job.config_version_id == active.id
    assert job.options["shadow_index"] is True
    assert job.options["publish_on_success"] is False
    assert job.options["cloud_processing_allowed"] is True
    assert job.options["full_cloud_run_confirmed"] is True
    assert job.model_signatures["OCR"] == "rapidocr:pp-ocrv6-zh"
    assert job.model_signatures["VISUAL_RETRIEVAL"] == "vidore/colqwen2.5-v0.2"


def test_embedding_probe_verifies_real_capability_and_dimension(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = ensure_default_config(db)
    config = config_from(version)
    profile = next(item for item in config.models if item.profile_id == "bailian_embedding")
    profile.base_url = "https://ws-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    version.content = config.model_dump(mode="json")
    version.content_hash = config.content_hash()
    db.commit()
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    ProbeClient.response_body = {
        "data": [{"index": 0, "embedding": [0.1] * 2560}],
        "usage": {"prompt_tokens": 8},
    }
    monkeypatch.setattr("docflow.services.config_service.httpx.Client", ProbeClient)

    result = probe_model(db, profile.profile_id)

    assert result.success is True
    assert result.capability_details["capability_verified"] is True
    assert result.capability_details["embedding_dimension"] == 2560
    assert result.capability_details["profile_fingerprint"]
    assert ProbeClient.last_url.endswith("/embeddings")
    assert ProbeClient.last_payload["dimensions"] == 2560


def test_chat_probe_uses_openai_compatible_chat_completion(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_default_config(db)
    monkeypatch.setenv("CHAT_LLM_API_KEY", "test-key")
    ProbeClient.response_body = {
        "choices": [{"message": {"content": "OK"}}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 1},
    }
    monkeypatch.setattr("docflow.services.config_service.httpx.Client", ProbeClient)

    result = probe_model(db, "cloud_chat_llm")

    assert result.success is True
    assert result.capability_details["capability_verified"] is True
    assert result.capability_details["profile_fingerprint"]
    assert ProbeClient.last_url == "https://api.siliconflow.cn/v1/chat/completions"
    assert ProbeClient.last_payload["enable_thinking"] is False
