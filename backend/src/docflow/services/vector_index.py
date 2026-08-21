from __future__ import annotations

import importlib.util
import threading
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    MultiVectorComparator,
    MultiVectorConfig,
    PointStruct,
    VectorParams,
)

from docflow.core.settings import get_settings
from docflow.domain.config import RuntimeConfigBundleV1, safe_collection_suffix


class VisualIndexError(RuntimeError):
    pass


@dataclass(frozen=True)
class VisualIndexResult:
    collection: str
    model_signature: str
    vector_count: int
    dimension: int


@dataclass(frozen=True)
class VisualSearchHit:
    page_id: str
    document_id: str
    score: float
    model_signature: str
    collection: str


@dataclass(frozen=True)
class TextVectorSearchHit:
    chunk_id: str
    page_id: str
    document_id: str
    score: float
    model_signature: str
    collection: str


@dataclass(frozen=True)
class CollectionStats:
    collection: str
    points_count: int
    dimension: int | None


def _distance(name: str) -> Distance:
    return {"Cosine": Distance.COSINE, "Dot": Distance.DOT, "Euclid": Distance.EUCLID}[name]


@lru_cache(maxsize=1)
def qdrant_client() -> QdrantClient:
    return QdrantClient(url=get_settings().qdrant_url, timeout=30)


def qdrant_id(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"docflow:{value}"))


def collection_stats(collection: str) -> CollectionStats:
    """Return exact publish-time evidence for a Qdrant collection."""
    client = qdrant_client()
    existing = {item.name for item in client.get_collections().collections}
    if collection not in existing:
        raise VisualIndexError(f"Qdrant Collection 不存在：{collection}")
    info = client.get_collection(collection_name=collection)
    vectors_config = info.config.params.vectors
    dimension = getattr(vectors_config, "size", None)
    return CollectionStats(
        collection=collection,
        points_count=int(info.points_count or 0),
        dimension=int(dimension) if dimension is not None else None,
    )


def text_collection_name(config: RuntimeConfigBundleV1, generation_id: str, signature: str) -> str:
    signature_part = safe_collection_suffix(signature)
    generation_part = safe_collection_suffix(generation_id)
    return f"{config.indexes.text_collection_prefix}_{signature_part}_{generation_part}"


def visual_collection_name(
    config: RuntimeConfigBundleV1, generation_id: str, signature: str
) -> str:
    signature_part = safe_collection_suffix(signature)
    generation_part = safe_collection_suffix(generation_id)
    return f"{config.indexes.visual_collection_prefix}_{signature_part}_{generation_part}"


def index_text_vectors(
    config: RuntimeConfigBundleV1,
    generation_id: str,
    signature: str,
    items: list[tuple[str, list[float], dict[str, Any]]],
) -> str:
    collection = text_collection_name(config, generation_id, signature)
    client = qdrant_client()
    existing = {item.name for item in client.get_collections().collections}
    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(
                size=config.indexes.embedding_dimension,
                distance=_distance(config.indexes.distance),
            ),
        )
    points = [
        PointStruct(id=qdrant_id(item_id), vector=vector, payload=payload)
        for item_id, vector, payload in items
    ]
    if points:
        client.upsert(collection_name=collection, points=points, wait=True)
    return collection


def search_text_vectors(
    config: RuntimeConfigBundleV1,
    generation_id: str,
    model_signature: str,
    query_vector: list[float],
    limit: int = 20,
) -> list[TextVectorSearchHit]:
    collection = text_collection_name(config, generation_id, model_signature)
    client = qdrant_client()
    existing = {item.name for item in client.get_collections().collections}
    if collection not in existing:
        raise VisualIndexError("当前索引代际没有匹配的文本向量 Collection，请先重建并发布索引")
    response = client.query_points(
        collection_name=collection,
        query=query_vector,
        limit=limit,
        with_payload=True,
    )
    hits: list[TextVectorSearchHit] = []
    for point in response.points:
        payload = point.payload or {}
        chunk_id = str(payload.get("chunk_id", ""))
        page_id = str(payload.get("page_id", ""))
        document_id = str(payload.get("document_id", ""))
        if not chunk_id or not page_id or not document_id:
            continue
        hits.append(
            TextVectorSearchHit(
                chunk_id=chunk_id,
                page_id=page_id,
                document_id=document_id,
                score=float(point.score),
                model_signature=model_signature,
                collection=collection,
            )
        )
    return hits


class ColPaliEncoder:
    def __init__(self, model_name: str) -> None:
        if importlib.util.find_spec("colpali_engine") is None:
            raise VisualIndexError("未安装 colpali-engine，请执行 uv sync --extra ml")
        import torch
        from colpali_engine.models import ColQwen2, ColQwen2Processor

        model_class = ColQwen2
        processor_class = ColQwen2Processor
        try:
            from colpali_engine.models import ColQwen2_5, ColQwen2_5_Processor

            if "2.5" in model_name:
                model_class = ColQwen2_5
                processor_class = ColQwen2_5_Processor
        except ImportError:
            if "2.5" in model_name:
                raise VisualIndexError("当前 colpali-engine 不支持 ColQwen2.5") from None
        self.torch = torch
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        dtype = torch.float32 if self.device == "cpu" else torch.bfloat16
        self.model = model_class.from_pretrained(
            model_name,
            torch_dtype=dtype,
            device_map=self.device,
        ).eval()
        self.processor = processor_class.from_pretrained(model_name)

    def encode(self, image_path: Path) -> list[list[float]]:
        image = Image.open(image_path).convert("RGB")
        batch = self.processor.process_images([image]).to(self.device)
        with self.torch.no_grad():
            embeddings = self.model(**batch)
        return embeddings[0].detach().cpu().float().tolist()

    def encode_query(self, query: str) -> list[list[float]]:
        batch = self.processor.process_queries([query]).to(self.device)
        with self.torch.no_grad():
            embeddings = self.model(**batch)
        return embeddings[0].detach().cpu().float().tolist()


_ENCODERS: dict[str, ColPaliEncoder] = {}
_ENCODER_LOCK = threading.Lock()


def _get_encoder(model_signature: str, model_name: str) -> ColPaliEncoder:
    encoder = _ENCODERS.get(model_signature)
    if encoder is not None:
        return encoder
    # FastAPI may receive several retrieval requests concurrently. Loading the
    # same multi-gigabyte model twice can exhaust unified memory on macOS.
    with _ENCODER_LOCK:
        encoder = _ENCODERS.get(model_signature)
        if encoder is None:
            encoder = ColPaliEncoder(model_name)
            _ENCODERS[model_signature] = encoder
        return encoder


def index_visual_page(
    config: RuntimeConfigBundleV1,
    generation_id: str,
    page_id: str,
    document_id: str,
    image_path: Path,
) -> VisualIndexResult:
    profile_id = config.routing.visual_retrieval_primary
    profile = next((item for item in config.models if item.profile_id == profile_id), None)
    if profile is None or not profile.enabled:
        raise VisualIndexError("未启用视觉检索模型")
    model_candidates = [profile]
    if profile.fallback_profile_id:
        fallback = next(
            (
                item
                for item in config.models
                if item.profile_id == profile.fallback_profile_id and item.enabled
            ),
            None,
        )
        if fallback:
            model_candidates.append(fallback)
    last_error: Exception | None = None
    vectors: list[list[float]] | None = None
    selected = profile
    for candidate in model_candidates:
        try:
            encoder = _get_encoder(candidate.model_signature, candidate.model_name)
            vectors = encoder.encode(image_path)
            selected = candidate
            break
        except Exception as exc:
            last_error = exc
    if not vectors:
        raise VisualIndexError(str(last_error or "视觉向量生成失败"))
    dimension = len(vectors[0])
    collection = visual_collection_name(config, generation_id, selected.model_signature)
    client = qdrant_client()
    existing = {item.name for item in client.get_collections().collections}
    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(
                size=dimension,
                distance=Distance.COSINE,
                multivector_config=MultiVectorConfig(comparator=MultiVectorComparator.MAX_SIM),
            ),
        )
    client.upsert(
        collection_name=collection,
        points=[
            PointStruct(
                id=qdrant_id(page_id),
                vector=vectors,
                payload={
                    "page_id": page_id,
                    "document_id": document_id,
                    "model_signature": selected.model_signature,
                },
            )
        ],
        wait=True,
    )
    return VisualIndexResult(collection, selected.model_signature, len(vectors), dimension)


def delete_visual_document_points(
    config: RuntimeConfigBundleV1,
    generation_id: str,
    document_id: str,
) -> None:
    """Best-effort compensation when the metadata transaction cannot commit."""
    from qdrant_client.http.models import FieldCondition, Filter, FilterSelector, MatchValue

    client = qdrant_client()
    try:
        existing = {item.name for item in client.get_collections().collections}
    except Exception:
        return
    signatures = {
        profile.model_signature
        for profile in config.models
        if profile.enabled and profile.capability.value == "VISUAL_RETRIEVAL"
    }
    selector = FilterSelector(
        filter=Filter(
            must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
        )
    )
    for signature in signatures:
        collection = visual_collection_name(config, generation_id, signature)
        if collection not in existing:
            continue
        try:
            client.delete(collection_name=collection, points_selector=selector, wait=True)
        except Exception:
            # Never hide the original PostgreSQL/parse failure with cleanup errors.
            continue


def search_visual_pages(
    config: RuntimeConfigBundleV1,
    generation_id: str,
    query: str,
    limit: int = 10,
) -> list[VisualSearchHit]:
    """Use the language side of ColPali/ColQwen to query page multi-vectors."""
    profile_id = config.routing.visual_retrieval_primary
    profile = next((item for item in config.models if item.profile_id == profile_id), None)
    if profile is None or not profile.enabled:
        raise VisualIndexError("未启用视觉检索模型")

    candidates = [profile]
    if profile.fallback_profile_id:
        fallback = next(
            (
                item
                for item in config.models
                if item.profile_id == profile.fallback_profile_id and item.enabled
            ),
            None,
        )
        if fallback:
            candidates.append(fallback)

    client = qdrant_client()
    try:
        existing = {item.name for item in client.get_collections().collections}
    except Exception as exc:
        raise VisualIndexError(f"Qdrant 不可用：{exc}") from exc

    hits: dict[str, VisualSearchHit] = {}
    errors: list[str] = []
    searched_collection = False
    for candidate in candidates:
        collection = visual_collection_name(config, generation_id, candidate.model_signature)
        if collection not in existing:
            continue
        searched_collection = True
        try:
            encoder = _get_encoder(candidate.model_signature, candidate.model_name)
            vectors = encoder.encode_query(query)
            response = client.query_points(
                collection_name=collection,
                query=vectors,
                limit=limit,
                with_payload=True,
            )
            for point in response.points:
                payload = point.payload or {}
                page_id = str(payload.get("page_id", ""))
                document_id = str(payload.get("document_id", ""))
                if not page_id or not document_id:
                    continue
                hit = VisualSearchHit(
                    page_id=page_id,
                    document_id=document_id,
                    score=float(point.score),
                    model_signature=candidate.model_signature,
                    collection=collection,
                )
                previous = hits.get(page_id)
                if previous is None or hit.score > previous.score:
                    hits[page_id] = hit
        except Exception as exc:
            errors.append(f"{candidate.display_name}: {exc}")

    if not searched_collection:
        raise VisualIndexError("当前索引代际没有可查询的 ColPali Collection，请先完成复杂页面入库")
    if not hits and errors:
        raise VisualIndexError("；".join(errors)[:800])
    return sorted(hits.values(), key=lambda item: item.score, reverse=True)[:limit]
