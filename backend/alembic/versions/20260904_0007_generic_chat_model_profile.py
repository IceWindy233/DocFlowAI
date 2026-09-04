"""Rewrite stored configurations onto the vendor-neutral chat model profile.

Revision ID: 20260904_0007
Revises: 20260816_0006
"""

import hashlib
import json
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "20260904_0007"
down_revision = "20260816_0006"
branch_labels = None
depends_on = None

LEGACY_CHAT_ADAPTERS = ("deepseek_openai", "siliconflow_openai")
GENERIC_ADAPTER = "openai_compatible"
LEGACY_PROFILE_ID = "deepseek_v4_flash"
PROFILE_ID = "cloud_chat_llm"
LEGACY_DISPLAY_NAMES = ("硅基流动 DeepSeek V4 Flash", "DeepSeek V4 Flash 问答生成")
DISPLAY_NAME = "云端对话模型（OpenAI 兼容）"
LEGACY_SECRET_ENV_NAMES = ("SILICONFLOW_API_KEY", "DEEPSEEK_API_KEY")
SECRET_ENV_NAME = "CHAT_LLM_API_KEY"
PROFILE_REFERENCE_FIELDS = (
    "structure_parser",
    "ocr_primary",
    "ocr_fallback",
    "vlm_primary",
    "visual_retrieval_primary",
    "text_embedding_primary",
    "reranker_primary",
    "qa_generation_primary",
)

_CONFIG_VERSIONS = sa.table(
    "config_versions",
    sa.column("id", sa.String),
    sa.column("content", sa.JSON),
    sa.column("content_hash", sa.String),
)


def _content_hash(content: dict[str, Any]) -> str:
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _collapse_duplicates(models: list[Any], profile_id: str) -> list[Any]:
    """Keep one profile per id: the enabled one wins, otherwise the earliest declared."""
    duplicates = [
        index
        for index, item in enumerate(models)
        if isinstance(item, dict) and item.get("profile_id") == profile_id
    ]
    if len(duplicates) < 2:
        return models
    keep = next(
        (index for index in duplicates if models[index].get("enabled")),
        duplicates[0],
    )
    dropped = set(duplicates) - {keep}
    return [item for index, item in enumerate(models) if index not in dropped]


def migrate_content(content: dict[str, Any], *, reverse: bool = False) -> dict[str, Any] | None:
    """Return the rewritten configuration snapshot, or None when nothing changed."""
    if not isinstance(content, dict):
        return None
    old_profile_id, new_profile_id = (
        (PROFILE_ID, LEGACY_PROFILE_ID) if reverse else (LEGACY_PROFILE_ID, PROFILE_ID)
    )
    old_display_names, new_display_name = (
        ((DISPLAY_NAME,), LEGACY_DISPLAY_NAMES[0])
        if reverse
        else (LEGACY_DISPLAY_NAMES, DISPLAY_NAME)
    )
    old_secret_names, new_secret_name = (
        ((SECRET_ENV_NAME,), LEGACY_SECRET_ENV_NAMES[0])
        if reverse
        else (LEGACY_SECRET_ENV_NAMES, SECRET_ENV_NAME)
    )

    updated = json.loads(json.dumps(content))
    models = updated.get("models")
    if not isinstance(models, list):
        models = []
    # 新代码可能在本迁移之前启动过，并按出厂默认补进了一份未启用的目标档案；
    # 用户实际在用的旧档案改名后必须唯一存在，因此先让出厂模板让位。
    if any(
        isinstance(item, dict) and item.get("profile_id") == old_profile_id for item in models
    ):
        models = [
            item
            for item in models
            if not (isinstance(item, dict) and item.get("profile_id") == new_profile_id)
        ]
    for profile in models:
        if not isinstance(profile, dict):
            continue
        if not reverse and profile.get("adapter_type") in LEGACY_CHAT_ADAPTERS:
            profile["adapter_type"] = GENERIC_ADAPTER
        if profile.get("profile_id") == old_profile_id:
            profile["profile_id"] = new_profile_id
            if profile.get("display_name") in old_display_names:
                profile["display_name"] = new_display_name
        if profile.get("fallback_profile_id") == old_profile_id:
            profile["fallback_profile_id"] = new_profile_id
        if profile.get("secret_env_name") in old_secret_names:
            profile["secret_env_name"] = new_secret_name
    updated["models"] = _collapse_duplicates(models, new_profile_id)

    routing = updated.get("routing")
    if isinstance(routing, dict):
        for field in PROFILE_REFERENCE_FIELDS:
            if routing.get(field) == old_profile_id:
                routing[field] = new_profile_id

    security = updated.get("security")
    if isinstance(security, dict):
        allowed = security.get("allowed_secret_env_names")
        if isinstance(allowed, list):
            security["allowed_secret_env_names"] = _unique(
                [new_secret_name if name in old_secret_names else name for name in allowed]
            )

    return updated if updated != content else None


def _rewrite_configurations(reverse: bool) -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("config_versions"):
        return
    rows = bind.execute(
        sa.select(_CONFIG_VERSIONS.c.id, _CONFIG_VERSIONS.c.content)
    ).fetchall()
    for version_id, content in rows:
        updated = migrate_content(content, reverse=reverse)
        if updated is None:
            continue
        bind.execute(
            _CONFIG_VERSIONS.update()
            .where(_CONFIG_VERSIONS.c.id == version_id)
            .values(content=updated, content_hash=_content_hash(updated))
        )


def _drop_stale_probes(profile_id: str) -> None:
    # 档案 ID 与密钥变量名都变了，旧探测指纹必然失配；删除后由用户重新做连通性测试。
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("model_probes"):
        return
    bind.execute(
        sa.text("DELETE FROM model_probes WHERE profile_id = :profile_id"),
        {"profile_id": profile_id},
    )


def upgrade() -> None:
    _rewrite_configurations(reverse=False)
    _drop_stale_probes(LEGACY_PROFILE_ID)


def downgrade() -> None:
    _rewrite_configurations(reverse=True)
    _drop_stale_probes(PROFILE_ID)
