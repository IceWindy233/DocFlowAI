from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from docflow.core.logging import redact
from docflow.core.settings import get_settings
from docflow.db.models import AuditEvent, ConfigVersion, IngestionJob, ModelProbe, new_id
from docflow.domain.config import (
    CLOUD_OPENAI_ADAPTERS,
    AdapterType,
    ChangeImpact,
    ConfigImpactResponse,
    ConfigVersionResponse,
    ModelCapability,
    ModelProfileV1,
    RuntimeConfigBundleV1,
    dashscope_workspace_rerank_base_url,
    default_runtime_config,
    flatten_dict,
)


class ConfigConflictError(RuntimeError):
    pass


class ConfigNotFoundError(RuntimeError):
    pass


class ModelNotReadyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    success: bool
    latency_ms: int
    details: dict[str, Any]
    error_message: str | None = None


REINDEX_PREFIXES = (
    "indexes",
    "chunking",
    "routing.text_embedding_primary",
    "routing.visual_retrieval_primary",
)
REPARSE_PREFIXES = (
    "parsing",
    "routing.structure_parser",
    "routing.ocr_primary",
    "routing.ocr_fallback",
    "routing.vlm_primary",
)


def version_to_response(version: ConfigVersion) -> ConfigVersionResponse:
    return ConfigVersionResponse(
        id=version.id,
        version=version.version,
        active=version.active,
        content_hash=version.content_hash,
        impact=ChangeImpact(version.impact),
        impact_details=version.impact_details or {},
        change_reason=version.change_reason,
        created_by=version.created_by,
        created_at=version.created_at.isoformat(),
        config=RuntimeConfigBundleV1.model_validate(version.content),
    )


def ensure_default_config(db: Session) -> ConfigVersion:
    current = db.scalar(select(ConfigVersion).where(ConfigVersion.active.is_(True)))
    if current:
        config = RuntimeConfigBundleV1.model_validate(current.content)
        defaults = default_runtime_config()
        known_profiles = {profile.profile_id for profile in config.models}
        missing_profiles = [
            profile.model_copy(deep=True)
            for profile in defaults.models
            if profile.profile_id not in known_profiles
        ]
        profile_updates: dict[str, dict[str, Any]] = {}
        profiles = _profile_map(config)
        embedding = profiles.get("bailian_embedding")
        reranker = profiles.get("bailian_reranker")
        for profile in missing_profiles:
            if profile.profile_id == "bailian_reranker" and embedding and embedding.workspace_id:
                profile.workspace_id = embedding.workspace_id
                profile.base_url = dashscope_workspace_rerank_base_url(embedding.workspace_id)
        if embedding and embedding.workspace_id and reranker and not reranker.workspace_id:
            profile_updates.setdefault(reranker.profile_id, {}).update(
                {
                    "workspace_id": embedding.workspace_id,
                    "base_url": dashscope_workspace_rerank_base_url(embedding.workspace_id),
                }
            )
        if embedding and embedding.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1":
            default_embedding = _profile_map(defaults)["bailian_embedding"]
            profile_updates.setdefault(embedding.profile_id, {}).update(
                {
                    "base_url": default_embedding.base_url,
                    "price_input_per_million": default_embedding.price_input_per_million,
                }
            )
        raw_profiles = {
            str(item.get("profile_id")): item
            for item in current.content.get("models", [])
            if isinstance(item, dict)
        }
        raw_embedding = raw_profiles.get("bailian_embedding", {})
        if (
            embedding
            and embedding.workspace_id
            and raw_embedding.get("workspace_id") != embedding.workspace_id
        ):
            profile_updates.setdefault(embedding.profile_id, {}).update(
                {
                    "workspace_id": embedding.workspace_id,
                    "base_url": embedding.base_url,
                }
            )
        allowed_secrets = list(config.security.allowed_secret_env_names)
        for env_name in defaults.security.allowed_secret_env_names:
            if env_name not in allowed_secrets:
                allowed_secrets.append(env_name)
        if (
            not missing_profiles
            and not profile_updates
            and allowed_secrets == config.security.allowed_secret_env_names
        ):
            return current

        upgraded = config.model_copy(deep=True)
        upgraded.models.extend(missing_profiles)
        for profile in upgraded.models:
            for field_name, value in profile_updates.get(profile.profile_id, {}).items():
                setattr(profile, field_name, value)
        upgraded.security.allowed_secret_env_names = allowed_secrets
        next_version = int(db.scalar(select(func.max(ConfigVersion.version))) or 0) + 1
        db.execute(update(ConfigVersion).where(ConfigVersion.active.is_(True)).values(active=False))
        version = ConfigVersion(
            version=next_version,
            content=upgraded.model_dump(mode="json"),
            content_hash=upgraded.content_hash(),
            impact=ChangeImpact.HOT.value,
            impact_details={
                "changed_paths": [
                    *(f"models.{profile.profile_id}" for profile in missing_profiles),
                    *(f"models.{profile_id}" for profile_id in profile_updates),
                    "security.allowed_secret_env_names",
                ],
                "reasons": ["补充新版本内置模型适配器和密钥环境变量白名单"],
            },
            active=True,
            parent_id=current.id,
            change_reason="系统升级内置模型档案",
        )
        db.add(version)
        db.flush()
        db.add(
            AuditEvent(
                event_type="CONFIG_SCHEMA_UPGRADED",
                target_type="configuration",
                target_id=version.id,
                before_hash=current.content_hash,
                after_hash=version.content_hash,
            )
        )
        db.commit()
        db.refresh(version)
        return version
    config = default_runtime_config()
    version = ConfigVersion(
        version=1,
        content=config.model_dump(mode="json"),
        content_hash=config.content_hash(),
        impact=ChangeImpact.HOT.value,
        impact_details={"changed_paths": [], "reasons": ["系统初始化"]},
        active=True,
        change_reason="系统初始化默认配置",
    )
    db.add(version)
    db.flush()
    db.add(
        AuditEvent(
            event_type="CONFIG_INITIALIZED",
            target_type="configuration",
            target_id=version.id,
            after_hash=version.content_hash,
        )
    )
    db.commit()
    db.refresh(version)
    return version


def get_current_config(db: Session) -> ConfigVersion:
    return ensure_default_config(db)


def get_version(db: Session, version_id: str) -> ConfigVersion:
    version = db.get(ConfigVersion, version_id)
    if not version:
        raise ConfigNotFoundError(f"配置版本不存在：{version_id}")
    return version


def _profile_map(config: RuntimeConfigBundleV1) -> dict[str, ModelProfileV1]:
    return {profile.profile_id: profile for profile in config.models}


_VECTOR_SPACE_PROFILE_FIELDS = {
    "provider_id",
    "adapter_type",
    "capability",
    "model_name",
    "embedding_dimension",
    "model_signature",
    "fallback_profile_id",
}
_PARSING_OUTPUT_PROFILE_FIELDS = {
    "provider_id",
    "adapter_type",
    "capability",
    "model_name",
    "model_signature",
    "fallback_profile_id",
}


def model_profile_probe_fingerprint(profile: ModelProfileV1) -> str:
    """Bind a successful probe to the actual non-secret provider endpoint and model."""
    fields = {
        "profile_id",
        "provider_id",
        "adapter_type",
        "capability",
        "model_name",
        "workspace_id",
        "base_url",
        "secret_env_name",
        "embedding_dimension",
        "model_signature",
        "request_options",
    }
    payload = profile.model_dump(mode="json", include=fields)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def calculate_impact(
    old: RuntimeConfigBundleV1,
    new: RuntimeConfigBundleV1,
) -> ConfigImpactResponse:
    old_flat = flatten_dict(old.model_dump(mode="json", exclude={"models"}))
    new_flat = flatten_dict(new.model_dump(mode="json", exclude={"models"}))
    changed_paths = sorted(
        path for path in set(old_flat) | set(new_flat) if old_flat.get(path) != new_flat.get(path)
    )

    old_profiles = _profile_map(old)
    new_profiles = _profile_map(new)
    model_impact = ChangeImpact.HOT
    for profile_id in sorted(set(old_profiles) | set(new_profiles)):
        before = old_profiles.get(profile_id)
        after = new_profiles.get(profile_id)
        if before == after:
            continue
        changed_paths.append(f"models.{profile_id}")
        capability = (after or before).capability
        changed_fields = (
            {
                field_name
                for field_name in type(before).model_fields
                if getattr(before, field_name) != getattr(after, field_name)
            }
            if before is not None and after is not None
            else set()
        )
        if (
            capability in {ModelCapability.TEXT_EMBEDDING, ModelCapability.VISUAL_RETRIEVAL}
            and changed_fields & _VECTOR_SPACE_PROFILE_FIELDS
        ):
            model_impact = ChangeImpact.REINDEX_REQUIRED
        elif (
            capability
            in {
                ModelCapability.OCR,
                ModelCapability.VISION_LM,
                ModelCapability.STRUCTURE_PARSER,
            }
            and changed_fields & _PARSING_OUTPUT_PROFILE_FIELDS
            and model_impact != ChangeImpact.REINDEX_REQUIRED
        ):
            model_impact = ChangeImpact.REPARSE_REQUIRED

    impact = model_impact
    if any(path.startswith(REINDEX_PREFIXES) for path in changed_paths):
        impact = ChangeImpact.REINDEX_REQUIRED
    elif impact != ChangeImpact.REINDEX_REQUIRED and any(
        path.startswith(REPARSE_PREFIXES) for path in changed_paths
    ):
        impact = ChangeImpact.REPARSE_REQUIRED

    reasons: list[str] = []
    if impact == ChangeImpact.REINDEX_REQUIRED:
        reasons.append("向量模型、切块或索引参数发生变化，需要建立新的索引代际")
    elif impact == ChangeImpact.REPARSE_REQUIRED:
        reasons.append("解析器或解析路由发生变化，需要重新解析受影响文档")
    else:
        reasons.append("变更仅影响后续任务的运行参数")
    return ConfigImpactResponse(
        impact=impact,
        changed_paths=sorted(set(changed_paths)),
        reasons=reasons,
        requires_rebuild=impact != ChangeImpact.HOT,
    )


def _require_cloud_defaults_ready(db: Session, config: RuntimeConfigBundleV1) -> None:
    profiles = _profile_map(config)
    routed_ids = {
        config.routing.vlm_primary,
        config.routing.text_embedding_primary,
        config.routing.reranker_primary,
        config.routing.qa_generation_primary,
    } - {None}
    for profile_id in routed_ids:
        profile = profiles[profile_id]
        if profile.adapter_type not in CLOUD_OPENAI_ADAPTERS:
            continue
        env_name = profile.secret_env_name
        if not env_name or env_name not in config.security.allowed_secret_env_names:
            raise ModelNotReadyError(f"模型 {profile_id} 使用了未授权的密钥环境变量")
        if not os.getenv(env_name):
            raise ModelNotReadyError(f"模型 {profile_id} 的环境变量 {env_name} 未配置")
        latest = db.scalar(
            select(ModelProbe)
            .where(ModelProbe.profile_id == profile_id, ModelProbe.success.is_(True))
            .order_by(ModelProbe.created_at.desc())
        )
        if (
            not latest
            or latest.capability_details.get("model_signature") != profile.model_signature
            or latest.capability_details.get("profile_fingerprint")
            != model_profile_probe_fingerprint(profile)
        ):
            raise ModelNotReadyError(f"模型 {profile_id} 必须先通过连通性测试")


def save_config(
    db: Session,
    *,
    base_version_id: str,
    config: RuntimeConfigBundleV1,
    change_reason: str,
    actor: str = "local-admin",
    enforce_model_readiness: bool = True,
) -> ConfigVersion:
    current = get_current_config(db)
    if current.id != base_version_id:
        raise ConfigConflictError("当前配置已被其他操作更新，请刷新后重试")
    if enforce_model_readiness:
        _require_cloud_defaults_ready(db, config)

    old_config = RuntimeConfigBundleV1.model_validate(current.content)
    impact = calculate_impact(old_config, config)
    if config.content_hash() == current.content_hash:
        return current

    next_version = int(db.scalar(select(func.max(ConfigVersion.version))) or 0) + 1
    db.execute(update(ConfigVersion).where(ConfigVersion.active.is_(True)).values(active=False))
    version = ConfigVersion(
        version=next_version,
        content=config.model_dump(mode="json"),
        content_hash=config.content_hash(),
        impact=impact.impact.value,
        impact_details=impact.model_dump(mode="json"),
        active=True,
        parent_id=current.id,
        created_by=actor,
        change_reason=change_reason,
    )
    db.add(version)
    db.flush()
    db.add(
        AuditEvent(
            event_type="CONFIG_ACTIVATED",
            actor=actor,
            target_type="configuration",
            target_id=version.id,
            before_hash=current.content_hash,
            after_hash=version.content_hash,
            details={"impact": impact.impact.value, "changed_paths": impact.changed_paths},
        )
    )
    db.commit()
    db.refresh(version)
    return version


def rollback_config(db: Session, version_id: str, actor: str = "local-admin") -> ConfigVersion:
    target = get_version(db, version_id)
    current = get_current_config(db)
    config = RuntimeConfigBundleV1.model_validate(target.content)
    return save_config(
        db,
        base_version_id=current.id,
        config=config,
        change_reason=f"回滚至配置 v{target.version}",
        actor=actor,
        enforce_model_readiness=False,
    )


def list_versions(db: Session, limit: int = 50) -> list[ConfigVersion]:
    return list(
        db.scalars(select(ConfigVersion).order_by(ConfigVersion.version.desc()).limit(limit))
    )


def get_profile(config: RuntimeConfigBundleV1, profile_id: str) -> ModelProfileV1:
    profile = next((item for item in config.models if item.profile_id == profile_id), None)
    if profile is None:
        raise ConfigNotFoundError(f"模型档案不存在：{profile_id}")
    return profile


def secret_status(config: RuntimeConfigBundleV1, profile_id: str) -> dict[str, Any]:
    profile = get_profile(config, profile_id)
    env_name = profile.secret_env_name
    if not env_name:
        return {"profile_id": profile_id, "required": False, "configured": True, "env_name": None}
    if env_name not in config.security.allowed_secret_env_names:
        return {
            "profile_id": profile_id,
            "required": True,
            "configured": False,
            "env_name": env_name,
        }
    return {
        "profile_id": profile_id,
        "required": True,
        "configured": bool(os.getenv(env_name)),
        "env_name": env_name,
    }


def _local_probe(profile: ModelProfileV1) -> ProbeResult:
    start = time.monotonic()
    success = True
    details: dict[str, Any] = {"adapter": profile.adapter_type.value}
    error: str | None = None
    if profile.adapter_type == AdapterType.DOCLING:
        success = importlib.util.find_spec("docling") is not None
        error = None if success else "未安装 docling，可执行 uv sync --extra ml"
    elif profile.adapter_type == AdapterType.RAPID_OCR:
        success = importlib.util.find_spec("rapidocr_onnxruntime") is not None
        error = None if success else "未安装 rapidocr-onnxruntime"
    elif profile.adapter_type == AdapterType.TESSERACT:
        success = shutil.which("tesseract") is not None
        error = None if success else "未找到 tesseract"
    elif profile.adapter_type == AdapterType.LIBREOFFICE:
        success = shutil.which("soffice") is not None
        error = None if success else "未找到 soffice"
    elif profile.adapter_type == AdapterType.LOCAL_TRANSFORMERS:
        success = importlib.util.find_spec("colpali_engine") is not None
        error = None if success else "未安装 colpali-engine，可执行 uv sync --extra ml"
    details["model_signature"] = profile.model_signature
    return ProbeResult(success, int((time.monotonic() - start) * 1000), details, error)


def probe_model(
    db: Session,
    profile_id: str,
    profile_override: ModelProfileV1 | None = None,
) -> ModelProbe:
    current = get_current_config(db)
    config = RuntimeConfigBundleV1.model_validate(current.content)
    profile = profile_override or get_profile(config, profile_id)
    if profile.profile_id != profile_id:
        raise ModelNotReadyError("探测模型 ID 与请求路径不一致")
    if profile.adapter_type not in CLOUD_OPENAI_ADAPTERS:
        result = _local_probe(profile)
    else:
        start = time.monotonic()
        env_name = profile.secret_env_name
        secret_allowed = bool(
            env_name and env_name in config.security.allowed_secret_env_names
        )
        if not secret_allowed:
            result = ProbeResult(False, 0, {}, f"环境变量 {env_name} 未获授权")
        elif not os.getenv(env_name):
            result = ProbeResult(False, 0, {}, f"环境变量 {env_name} 未配置")
        elif (
            profile.adapter_type == AdapterType.DASHSCOPE_OPENAI
            and profile.capability in {
                ModelCapability.TEXT_EMBEDDING,
                ModelCapability.RERANKER,
            }
            and (
                profile.capability == ModelCapability.RERANKER
                or profile.model_name == "qwen3.7-text-embedding"
            )
            and not profile.workspace_id
        ):
            result = ProbeResult(False, 0, {}, "百炼 Workspace 模型尚未配置 Workspace ID")
        elif (
            not profile.base_url
            or "YOUR_WORKSPACE_ID" in profile.base_url
            or "{" in profile.base_url
        ):
            result = ProbeResult(False, 0, {}, "模型 Base URL 尚未填写有效的 Workspace ID")
        else:
            try:
                with httpx.Client(timeout=min(profile.timeout_seconds, 30)) as client:
                    headers = {
                        "Authorization": f"Bearer {os.environ[profile.secret_env_name]}",
                        "Content-Type": "application/json",
                    }
                    if profile.capability == ModelCapability.TEXT_EMBEDDING:
                        payload: dict[str, Any] = {
                            "model": profile.model_name,
                            "input": ["DocFlow AI 模型连通性测试"],
                            "encoding_format": "float",
                        }
                        if profile.embedding_dimension:
                            payload["dimensions"] = profile.embedding_dimension
                        response = client.post(
                            f"{profile.base_url.rstrip('/')}/embeddings",
                            headers=headers,
                            json=payload,
                        )
                    elif profile.capability == ModelCapability.RERANKER:
                        response = client.post(
                            f"{profile.base_url.rstrip('/')}/reranks",
                            headers=headers,
                            json={
                                "model": profile.model_name,
                                "query": "公文审核",
                                "documents": ["这是一份公文审核测试材料。", "天气晴朗。"],
                                "top_n": 2,
                            },
                        )
                    elif profile.capability == ModelCapability.CHAT_LLM:
                        payload = {
                            "model": profile.model_name,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": "这是连通性测试，请只回复 OK。",
                                }
                            ],
                            "temperature": 0,
                            "max_tokens": 8,
                        }
                        payload.update(profile.request_options)
                        response = client.post(
                            f"{profile.base_url.rstrip('/')}/chat/completions",
                            headers=headers,
                            json=payload,
                        )
                    else:
                        response = client.get(
                            f"{profile.base_url.rstrip('/')}/models",
                            headers=headers,
                        )
                    response.raise_for_status()
                    body = response.json()
                    details: dict[str, Any] = {
                        "model_signature": profile.model_signature,
                        "endpoint_reachable": True,
                        "capability_verified": True,
                    }
                    if profile.capability == ModelCapability.TEXT_EMBEDDING:
                        vector = body["data"][0]["embedding"]
                        if (
                            profile.embedding_dimension
                            and len(vector) != profile.embedding_dimension
                        ):
                            raise ValueError("云端返回的向量维度与配置不一致")
                        details["embedding_dimension"] = len(vector)
                    elif profile.capability == ModelCapability.CHAT_LLM:
                        if not str(body["choices"][0]["message"]["content"]).strip():
                            raise ValueError("问答模型返回了空内容")
                    elif profile.capability == ModelCapability.RERANKER:
                        results = body.get("results") or []
                        if not results or "relevance_score" not in results[0]:
                            raise ValueError("重排序模型返回格式异常")
                        details["candidate_count"] = len(results)
                result = ProbeResult(
                    True,
                    int((time.monotonic() - start) * 1000),
                    details,
                )
            except Exception as exc:  # provider errors must become data, not crash the API
                result = ProbeResult(
                    False,
                    int((time.monotonic() - start) * 1000),
                    {"model_signature": profile.model_signature},
                    str(redact(str(exc)))[:500],
                )
    result = ProbeResult(
        result.success,
        result.latency_ms,
        {
            **result.details,
            "profile_fingerprint": model_profile_probe_fingerprint(profile),
        },
        result.error_message,
    )
    probe = ModelProbe(
        profile_id=profile_id,
        config_version_id=current.id,
        success=result.success,
        latency_ms=result.latency_ms,
        capability_details=result.details,
        error_message=result.error_message,
    )
    db.add(probe)
    db.commit()
    db.refresh(probe)
    return probe


def create_rebuild_job(db: Session, actor: str = "local-admin") -> IngestionJob:
    current = get_current_config(db)
    config = RuntimeConfigBundleV1.model_validate(current.content)
    profiles = _profile_map(config)
    routed_profile_ids = (
        config.routing.structure_parser,
        config.routing.ocr_primary,
        config.routing.vlm_primary,
        config.routing.visual_retrieval_primary,
        config.routing.text_embedding_primary,
        config.routing.reranker_primary,
        config.routing.qa_generation_primary,
    )
    signatures: dict[str, str] = {}
    for profile_id in routed_profile_ids:
        if not profile_id:
            continue
        profile = profiles.get(profile_id)
        if profile and profile.enabled:
            signatures[profile.capability.value] = profile.model_signature
    latest_corpus_job = db.scalar(
        select(IngestionJob)
        .where(IngestionJob.job_type.in_(["FULL_SCAN", "INCREMENTAL_SCAN"]))
        .order_by(IngestionJob.created_at.desc())
    )
    source_root = (
        latest_corpus_job.source_root if latest_corpus_job else str(get_settings().source_root)
    )
    source_roots = (
        latest_corpus_job.options.get("source_roots", [source_root])
        if latest_corpus_job
        else [source_root]
    )
    job = IngestionJob(
        job_type="REBUILD",
        source_root=source_root,
        config_version_id=current.id,
        index_generation_id=new_id("idx"),
        options={
            "shadow_index": True,
            "publish_on_success": False,
            "source_roots": source_roots,
            # 点击“一键重建”本身就是管理员对该影子任务的显式授权；
            # 仍同时受配置中心的调用次数、Token 与费用上限约束。
            "cloud_processing_allowed": config.budget.cloud_processing_allowed,
            "full_cloud_run_confirmed": True,
        },
        progress={"total": 0, "completed": 0, "failed": 0},
        stage_counts={},
        model_signatures=signatures,
        cloud_usage={"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_cny": 0},
    )
    db.add(job)
    db.flush()
    db.add(
        AuditEvent(
            event_type="CONFIG_REBUILD_REQUESTED",
            actor=actor,
            target_type="ingestion_job",
            target_id=job.id,
            details={"config_version_id": current.id},
        )
    )
    db.commit()
    db.refresh(job)
    return job
