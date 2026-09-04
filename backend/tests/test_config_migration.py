from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy.orm import Session

from docflow.db.models import ConfigVersion, ModelProbe
from docflow.domain.config import RuntimeConfigBundleV1, default_runtime_config

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260904_0007_generic_chat_model_profile.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generic_chat_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


def _legacy_content() -> dict:
    content = default_runtime_config().model_dump(mode="json")
    generation = next(item for item in content["models"] if item["profile_id"] == "cloud_chat_llm")
    generation.update(
        {
            "profile_id": "deepseek_v4_flash",
            "display_name": "硅基流动 DeepSeek V4 Flash",
            "adapter_type": "siliconflow_openai",
            "secret_env_name": "SILICONFLOW_API_KEY",
            "enabled": True,
        }
    )
    content["routing"]["qa_generation_primary"] = "deepseek_v4_flash"
    content["security"]["allowed_secret_env_names"] = ["DASHSCOPE_API_KEY", "SILICONFLOW_API_KEY"]
    return content


def test_stored_snapshot_is_rewritten_onto_the_generic_chat_profile() -> None:
    upgraded = migration.migrate_content(_legacy_content())

    assert upgraded is not None
    generation = next(
        item for item in upgraded["models"] if item["profile_id"] == "cloud_chat_llm"
    )
    assert generation["adapter_type"] == "openai_compatible"
    assert generation["display_name"] == "云端对话模型（OpenAI 兼容）"
    assert generation["secret_env_name"] == "CHAT_LLM_API_KEY"
    assert generation["enabled"] is True
    assert upgraded["routing"]["qa_generation_primary"] == "cloud_chat_llm"
    assert upgraded["security"]["allowed_secret_env_names"] == [
        "DASHSCOPE_API_KEY",
        "CHAT_LLM_API_KEY",
    ]


def test_rewritten_snapshot_matches_the_current_schema() -> None:
    upgraded = migration.migrate_content(_legacy_content())

    assert upgraded is not None
    bundle = RuntimeConfigBundleV1.model_validate(upgraded)
    assert bundle.routing.qa_generation_primary == "cloud_chat_llm"
    assert migration._content_hash(upgraded) == bundle.content_hash()


def test_already_migrated_snapshot_is_left_untouched() -> None:
    current = default_runtime_config().model_dump(mode="json")

    assert migration.migrate_content(current) is None


def test_downgrade_restores_the_legacy_identifiers() -> None:
    upgraded = migration.migrate_content(_legacy_content())
    assert upgraded is not None

    reverted = migration.migrate_content(upgraded, reverse=True)

    assert reverted is not None
    generation = next(
        item for item in reverted["models"] if item["profile_id"] == "deepseek_v4_flash"
    )
    assert generation["display_name"] == "硅基流动 DeepSeek V4 Flash"
    assert generation["secret_env_name"] == "SILICONFLOW_API_KEY"
    assert reverted["routing"]["qa_generation_primary"] == "deepseek_v4_flash"
    assert reverted["security"]["allowed_secret_env_names"] == [
        "DASHSCOPE_API_KEY",
        "SILICONFLOW_API_KEY",
    ]


def test_duplicate_secret_names_are_collapsed_after_rewrite() -> None:
    content = _legacy_content()
    content["security"]["allowed_secret_env_names"] = [
        "DASHSCOPE_API_KEY",
        "SILICONFLOW_API_KEY",
        "DEEPSEEK_API_KEY",
    ]

    upgraded = migration.migrate_content(content)

    assert upgraded is not None
    assert upgraded["security"]["allowed_secret_env_names"] == [
        "DASHSCOPE_API_KEY",
        "CHAT_LLM_API_KEY",
    ]


def test_factory_template_added_before_migrating_yields_to_the_live_profile() -> None:
    # 新代码热重载会先按出厂默认补进一份未启用档案，随后迁移才改名旧档案。
    content = _legacy_content()
    template = next(
        item
        for item in default_runtime_config().model_dump(mode="json")["models"]
        if item["profile_id"] == "cloud_chat_llm"
    )
    content["models"].append(template)
    content["models"][-1]["price_input_per_million"] = 99.0

    upgraded = migration.migrate_content(content)

    assert upgraded is not None
    survivors = [
        item for item in upgraded["models"] if item["profile_id"] == "cloud_chat_llm"
    ]
    assert len(survivors) == 1
    assert survivors[0]["enabled"] is True
    assert survivors[0]["price_input_per_million"] == 0.0
    RuntimeConfigBundleV1.model_validate(upgraded)


def test_duplicate_profiles_left_by_a_partial_rewrite_are_collapsed() -> None:
    content = _legacy_content()
    disabled_twin = next(
        item
        for item in default_runtime_config().model_dump(mode="json")["models"]
        if item["profile_id"] == "cloud_chat_llm"
    )
    live_twin = dict(disabled_twin, enabled=True, model_name="acme-writer-large")
    content["models"] = [
        item for item in content["models"] if item["profile_id"] != "deepseek_v4_flash"
    ]
    content["models"].extend([disabled_twin, live_twin])

    upgraded = migration.migrate_content(content)

    assert upgraded is not None
    survivors = [
        item for item in upgraded["models"] if item["profile_id"] == "cloud_chat_llm"
    ]
    assert len(survivors) == 1
    assert survivors[0]["model_name"] == "acme-writer-large"
    RuntimeConfigBundleV1.model_validate(upgraded)


def test_migration_rewrites_stored_rows_and_clears_stale_probes(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = ConfigVersion(
        version=1,
        content=_legacy_content(),
        content_hash="legacy",
        impact="HOT",
        impact_details={},
        active=True,
        change_reason="seed",
    )
    db.add(version)
    db.flush()
    db.add(
        ModelProbe(
            profile_id="deepseek_v4_flash",
            config_version_id=version.id,
            success=True,
            latency_ms=1,
            capability_details={"model_signature": "siliconflow:deepseek-ai/DeepSeek-V4-Flash"},
        )
    )
    db.commit()

    class _Op:
        @staticmethod
        def get_bind():
            return db.connection()

    monkeypatch.setattr(migration, "op", _Op)
    migration.upgrade()
    db.commit()
    db.expire_all()

    stored = db.query(ConfigVersion).one()
    bundle = RuntimeConfigBundleV1.model_validate(stored.content)
    profile = next(item for item in bundle.models if item.profile_id == "cloud_chat_llm")
    assert profile.adapter_type.value == "openai_compatible"
    assert profile.secret_env_name == "CHAT_LLM_API_KEY"
    assert profile.enabled is True
    assert bundle.routing.qa_generation_primary == "cloud_chat_llm"
    assert stored.content_hash == bundle.content_hash()
    assert db.query(ModelProbe).count() == 0
