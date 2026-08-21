from __future__ import annotations

import enum
import hashlib
import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ProfileId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{1,63}$")]
EnvName = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,127}$")]
WorkspaceId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{2,127}$"),
]

DASHSCOPE_WORKSPACE_URL = re.compile(
    r"^https://(?P<workspace_id>[a-z0-9][a-z0-9-]{2,127})\."
    r"cn-beijing\.maas\.aliyuncs\.com/(?:compatible-mode|compatible-api)/v1/?$"
)


def dashscope_workspace_base_url(workspace_id: str) -> str:
    return (
        f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/"
        "compatible-mode/v1"
    )


def dashscope_workspace_rerank_base_url(workspace_id: str) -> str:
    return (
        f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/"
        "compatible-api/v1"
    )


class AdapterType(enum.StrEnum):
    DASHSCOPE_OPENAI = "dashscope_openai"
    DEEPSEEK_OPENAI = "deepseek_openai"
    LOCAL_TRANSFORMERS = "local_transformers"
    RAPID_OCR = "rapidocr"
    TESSERACT = "tesseract"
    DOCLING = "docling"
    LIBREOFFICE = "libreoffice"


class ModelCapability(enum.StrEnum):
    VISION_LM = "VISION_LM"
    TEXT_EMBEDDING = "TEXT_EMBEDDING"
    VISUAL_RETRIEVAL = "VISUAL_RETRIEVAL"
    OCR = "OCR"
    STRUCTURE_PARSER = "STRUCTURE_PARSER"
    CHAT_LLM = "CHAT_LLM"
    RERANKER = "RERANKER"


class ChangeImpact(enum.StrEnum):
    HOT = "HOT"
    REPARSE_REQUIRED = "REPARSE_REQUIRED"
    REINDEX_REQUIRED = "REINDEX_REQUIRED"


class ModelProfileV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: ProfileId
    display_name: str = Field(min_length=1, max_length=100)
    provider_id: str = Field(min_length=1, max_length=64)
    adapter_type: AdapterType
    capability: ModelCapability
    model_name: str = Field(min_length=1, max_length=200)
    workspace_id: WorkspaceId | None = None
    base_url: str | None = None
    secret_env_name: EnvName | None = None
    enabled: bool = True
    fallback_profile_id: ProfileId | None = None
    temperature: float = Field(default=0.1, ge=0, le=2)
    max_output_tokens: int = Field(default=4096, ge=1, le=131072)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    max_retries: int = Field(default=2, ge=0, le=10)
    concurrency: int = Field(default=2, ge=1, le=64)
    requests_per_minute: int = Field(default=60, ge=1, le=100000)
    embedding_dimension: int | None = Field(default=None, ge=1, le=65536)
    model_signature: str = Field(min_length=1, max_length=240)
    price_input_per_million: float = Field(default=0.0, ge=0)
    price_output_per_million: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def validate_capability_fields(self) -> ModelProfileV1:
        if self.adapter_type == AdapterType.DASHSCOPE_OPENAI and self.capability in {
            ModelCapability.TEXT_EMBEDDING,
            ModelCapability.RERANKER,
        }:
            if self.workspace_id:
                # Workspace ID 是该适配器的单一配置源，避免用户手工拼错 Endpoint。
                self.base_url = (
                    dashscope_workspace_rerank_base_url(self.workspace_id)
                    if self.capability == ModelCapability.RERANKER
                    else dashscope_workspace_base_url(self.workspace_id)
                )
            elif self.base_url:
                match = DASHSCOPE_WORKSPACE_URL.fullmatch(self.base_url)
                if match:
                    # 兼容升级前只保存完整 Base URL 的不可变配置快照。
                    self.workspace_id = match.group("workspace_id")
        if self.capability == ModelCapability.TEXT_EMBEDDING and not self.embedding_dimension:
            raise ValueError("文本向量模型必须声明 embedding_dimension")
        if self.adapter_type in {
            AdapterType.DASHSCOPE_OPENAI,
            AdapterType.DEEPSEEK_OPENAI,
        } and not self.secret_env_name:
            raise ValueError("云端模型必须配置 secret_env_name")
        if self.fallback_profile_id == self.profile_id:
            raise ValueError("模型不能将自身设为降级模型")
        return self


class RoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    structure_parser: ProfileId
    ocr_primary: ProfileId
    ocr_fallback: ProfileId | None = None
    vlm_primary: ProfileId | None = None
    visual_retrieval_primary: ProfileId | None = None
    text_embedding_primary: ProfileId | None = None
    reranker_primary: ProfileId | None = None
    qa_generation_primary: ProfileId | None = None
    upgrade_order: list[Literal["NATIVE", "OCR", "REGION_OCR", "VLM"]] = Field(
        default_factory=lambda: ["NATIVE", "OCR", "REGION_OCR", "VLM"]
    )


class ParserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_file_size_mb: int = Field(default=500, ge=1, le=4096)
    max_page_count: int = Field(default=2000, ge=1, le=10000)
    timeout_seconds: int = Field(default=600, ge=10, le=7200)
    pdf_render_dpi: int = Field(default=250, ge=96, le=600)
    searchable_chars_per_page_min: int = Field(default=20, ge=0, le=1000)
    archive_max_depth: int = Field(default=2, ge=0, le=5)
    archive_max_entries: int = Field(default=2000, ge=1, le=100000)
    archive_max_uncompressed_mb: int = Field(default=2048, ge=1, le=102400)
    macros_allowed: bool = False
    embedded_files_allowed: bool = False


class QualityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pass_score: float = Field(default=0.85, ge=0, le=1)
    warning_score: float = Field(default=0.70, ge=0, le=1)
    retry_score: float = Field(default=0.55, ge=0, le=1)
    complex_table_threshold: float = Field(default=0.65, ge=0, le=1)
    visual_required_threshold: float = Field(default=0.72, ge=0, le=1)

    @model_validator(mode="after")
    def validate_order(self) -> QualityConfig:
        if not self.pass_score > self.warning_score > self.retry_score:
            raise ValueError("质量阈值必须满足 pass > warning > retry")
        return self


class ChunkingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_min_chars: int = Field(default=300, ge=50, le=4000)
    target_max_chars: int = Field(default=800, ge=100, le=12000)
    preserve_heading_boundary: bool = True
    repeat_table_headers: bool = True
    table_serialization: Literal["markdown", "row_text", "html"] = "row_text"

    @model_validator(mode="after")
    def validate_range(self) -> ChunkingConfig:
        if self.target_min_chars >= self.target_max_chars:
            raise ValueError("target_min_chars 必须小于 target_max_chars")
        return self


class IndexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text_collection_prefix: str = "docflow_text"
    visual_collection_prefix: str = "docflow_visual"
    distance: Literal["Cosine", "Dot", "Euclid"] = "Cosine"
    embedding_dimension: int = Field(default=2560, ge=1, le=65536)
    visual_enabled: bool = True
    visual_only_complex_pages: bool = True
    visual_required_before_publish: bool = True


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu_worker_concurrency: int = Field(default=4, ge=1, le=64)
    ml_worker_concurrency: int = Field(default=1, ge=1, le=8)
    retry_backoff_seconds: int = Field(default=10, ge=1, le=3600)
    circuit_breaker_failures: int = Field(default=5, ge=1, le=100)
    cloud_calls_per_minute: int = Field(default=30, ge=1, le=10000)


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cloud_processing_allowed: bool = False
    benchmark_cloud_call_limit: int = Field(default=20, ge=0, le=10000)
    full_run_requires_confirmation: bool = True
    max_cloud_calls_per_job: int = Field(default=20, ge=0, le=10000000)
    max_input_tokens_per_job: int = Field(default=1_000_000, ge=0)
    estimated_cost_cny_limit: float = Field(default=100.0, ge=0)


class PublicationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported_publish_rate_min: float = Field(default=0.95, ge=0, le=1)
    authority_score_review_threshold: float = Field(default=0.90, ge=0, le=1)
    require_no_missing_page_alignment: bool = True
    require_visual_ready_when_required: bool = True


class SecurityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bind_localhost_only: bool = True
    allowed_secret_env_names: list[EnvName] = Field(
        default_factory=lambda: ["DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY"]
    )
    log_document_content: bool = False
    prompt_response_trace_enabled: bool = False


class RuntimeConfigBundleV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    profile_name: str = "default_zh_official_docs"
    models: list[ModelProfileV1]
    routing: RoutingConfig
    parsing: ParserConfig = Field(default_factory=ParserConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    indexes: IndexConfig = Field(default_factory=IndexConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    publication: PublicationConfig = Field(default_factory=PublicationConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    prompt_templates: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_model_graph(self) -> RuntimeConfigBundleV1:
        profiles = {profile.profile_id: profile for profile in self.models}
        if len(profiles) != len(self.models):
            raise ValueError("模型 profile_id 不能重复")

        expected: dict[str, ModelCapability] = {
            "structure_parser": ModelCapability.STRUCTURE_PARSER,
            "ocr_primary": ModelCapability.OCR,
            "ocr_fallback": ModelCapability.OCR,
            "vlm_primary": ModelCapability.VISION_LM,
            "visual_retrieval_primary": ModelCapability.VISUAL_RETRIEVAL,
            "text_embedding_primary": ModelCapability.TEXT_EMBEDDING,
            "reranker_primary": ModelCapability.RERANKER,
            "qa_generation_primary": ModelCapability.CHAT_LLM,
        }
        for field_name, capability in expected.items():
            profile_id = getattr(self.routing, field_name)
            if profile_id is None:
                continue
            profile = profiles.get(profile_id)
            if profile is None:
                raise ValueError(f"路由 {field_name} 引用了不存在的模型 {profile_id}")
            if not profile.enabled:
                raise ValueError(f"路由 {field_name} 引用了已禁用的模型 {profile_id}")
            if profile.capability != capability:
                raise ValueError(f"路由 {field_name} 与模型能力不匹配")

        for profile in self.models:
            if profile.fallback_profile_id and profile.fallback_profile_id not in profiles:
                raise ValueError(f"降级模型 {profile.fallback_profile_id} 不存在")

        embedding_id = self.routing.text_embedding_primary
        if embedding_id:
            embedding_profile = profiles[embedding_id]
            dimension = embedding_profile.embedding_dimension
            if dimension != self.indexes.embedding_dimension:
                raise ValueError("文本模型维度必须与索引维度一致")
            if (
                embedding_profile.adapter_type == AdapterType.DASHSCOPE_OPENAI
                and embedding_profile.model_name == "qwen3.7-text-embedding"
                and not embedding_profile.workspace_id
            ):
                raise ValueError("百炼 qwen3.7 文本向量路由必须配置 Workspace ID")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class ConfigSaveRequest(BaseModel):
    base_version_id: str
    change_reason: str = Field(min_length=2, max_length=500)
    config: RuntimeConfigBundleV1


class ConfigValidateRequest(BaseModel):
    config: RuntimeConfigBundleV1


class ModelProbeRequest(BaseModel):
    profile: ModelProfileV1


class ConfigImpactResponse(BaseModel):
    impact: ChangeImpact
    changed_paths: list[str]
    reasons: list[str]
    requires_rebuild: bool


class ConfigVersionResponse(BaseModel):
    id: str
    version: int
    active: bool
    content_hash: str
    impact: ChangeImpact
    impact_details: dict[str, Any]
    change_reason: str
    created_by: str
    created_at: str
    config: RuntimeConfigBundleV1


def default_runtime_config() -> RuntimeConfigBundleV1:
    return RuntimeConfigBundleV1(
        models=[
            ModelProfileV1(
                profile_id="docling_default",
                display_name="Docling 结构解析",
                provider_id="local",
                adapter_type=AdapterType.DOCLING,
                capability=ModelCapability.STRUCTURE_PARSER,
                model_name="docling",
                model_signature="docling:runtime",
            ),
            ModelProfileV1(
                profile_id="rapidocr_zh",
                display_name="RapidOCR 中文识别",
                provider_id="local",
                adapter_type=AdapterType.RAPID_OCR,
                capability=ModelCapability.OCR,
                model_name="pp-ocrv6-zh",
                model_signature="rapidocr:pp-ocrv6-zh",
            ),
            ModelProfileV1(
                profile_id="tesseract_zh",
                display_name="Tesseract 中文降级",
                provider_id="local",
                adapter_type=AdapterType.TESSERACT,
                capability=ModelCapability.OCR,
                model_name="chi_sim+eng",
                model_signature="tesseract:chi_sim+eng",
            ),
            ModelProfileV1(
                profile_id="bailian_vlm",
                display_name="百炼复杂页面 VLM",
                provider_id="dashscope",
                adapter_type=AdapterType.DASHSCOPE_OPENAI,
                capability=ModelCapability.VISION_LM,
                model_name="qwen3.7-plus-2026-05-26",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                secret_env_name="DASHSCOPE_API_KEY",
                model_signature="dashscope:qwen3.7-plus-2026-05-26",
                enabled=False,
            ),
            ModelProfileV1(
                profile_id="bailian_embedding",
                display_name="百炼文本向量",
                provider_id="dashscope",
                adapter_type=AdapterType.DASHSCOPE_OPENAI,
                capability=ModelCapability.TEXT_EMBEDDING,
                model_name="qwen3.7-text-embedding",
                workspace_id=None,
                base_url=(
                    "https://YOUR_WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/"
                    "compatible-mode/v1"
                ),
                secret_env_name="DASHSCOPE_API_KEY",
                embedding_dimension=2560,
                model_signature="dashscope:qwen3.7-text-embedding:2560",
                price_input_per_million=0.5,
                enabled=False,
            ),
            ModelProfileV1(
                profile_id="bailian_reranker",
                display_name="百炼 Qwen3 轻量重排序",
                provider_id="dashscope",
                adapter_type=AdapterType.DASHSCOPE_OPENAI,
                capability=ModelCapability.RERANKER,
                model_name="qwen3-rerank",
                workspace_id=None,
                base_url=(
                    "https://YOUR_WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/"
                    "compatible-api/v1"
                ),
                secret_env_name="DASHSCOPE_API_KEY",
                model_signature="dashscope:qwen3-rerank",
                timeout_seconds=60,
                enabled=False,
            ),
            ModelProfileV1(
                profile_id="deepseek_v4_flash",
                display_name="DeepSeek V4 Flash 问答生成",
                provider_id="deepseek",
                adapter_type=AdapterType.DEEPSEEK_OPENAI,
                capability=ModelCapability.CHAT_LLM,
                model_name="deepseek-v4-flash",
                base_url="https://api.deepseek.com",
                secret_env_name="DEEPSEEK_API_KEY",
                temperature=0.1,
                max_output_tokens=4096,
                model_signature="deepseek:deepseek-v4-flash",
                enabled=False,
            ),
            ModelProfileV1(
                profile_id="colqwen25_visual",
                display_name="ColQwen2.5 页面视觉索引",
                provider_id="local",
                adapter_type=AdapterType.LOCAL_TRANSFORMERS,
                capability=ModelCapability.VISUAL_RETRIEVAL,
                model_name="vidore/colqwen2.5-v0.2",
                fallback_profile_id="colqwen2_visual",
                model_signature="vidore/colqwen2.5-v0.2",
                timeout_seconds=600,
                concurrency=1,
            ),
            ModelProfileV1(
                profile_id="colqwen2_visual",
                display_name="ColQwen2 页面视觉降级",
                provider_id="local",
                adapter_type=AdapterType.LOCAL_TRANSFORMERS,
                capability=ModelCapability.VISUAL_RETRIEVAL,
                model_name="vidore/colqwen2-v1.0",
                model_signature="vidore/colqwen2-v1.0",
                timeout_seconds=600,
                concurrency=1,
            ),
        ],
        routing=RoutingConfig(
            structure_parser="docling_default",
            ocr_primary="rapidocr_zh",
            ocr_fallback="tesseract_zh",
            vlm_primary=None,
            visual_retrieval_primary="colqwen25_visual",
            text_embedding_primary=None,
            reranker_primary=None,
            qa_generation_primary=None,
        ),
    )


def flatten_dict(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            result.update(flatten_dict(child, path))
        return result
    if isinstance(value, list):
        return {prefix: value}
    return {prefix: value}


def safe_collection_suffix(signature: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", signature.lower()).strip("_")[:48]
