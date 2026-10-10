"""检索模块：先执行知识库权限过滤，再完成混合召回、RRF 融合和可选重排。"""

from __future__ import annotations

from datetime import date
import hashlib
import json
import re

import httpx
import jieba
from rank_bm25 import BM25Okapi
from sqlalchemy import select

from ..config import get_settings
from ..models import Chunk, Document, DocumentVersion, KnowledgeBase


def tokenize(text: str) -> list[str]:
    """把中英文文本切成 BM25 词项；仅供未接入 Milvus 时的本地开发模式使用。"""
    stop_words = {"的", "了", "和", "是", "在", "有", "请", "帮", "我"}
    return [
        word.lower()
        for word in jieba.lcut(text)
        if re.search(r"\w", word) and word not in stop_words
    ]


def create_embeddings(texts: list[str]) -> list[list[float]]:
    """通过 OpenAI 兼容的 Embeddings 接口分批生成向量，并严格检查数量与维度。"""
    settings = get_settings()
    if not texts:
        return []
    if settings.embedding_backend == "local":
        from .module1_local_embedding import create_local_embeddings

        return create_local_embeddings(texts)
    if not (
        settings.embedding_base_url
        and settings.embedding_api_key
        and settings.embedding_model
    ):
        raise ValueError("Milvus 模式需要配置 Embedding 接口、模型和 API Key")

    vectors: list[list[float]] = []
    with httpx.Client(timeout=60) as client:
        for offset in range(0, len(texts), 16):
            batch_texts = texts[offset : offset + 16]
            response = client.post(
                settings.embedding_base_url.rstrip("/") + "/embeddings",
                headers={"Authorization": "Bearer " + settings.embedding_api_key},
                json={"model": settings.embedding_model, "input": batch_texts},
            )
            if response.status_code != 200:
                raise ValueError(f"Embedding 服务返回 HTTP {response.status_code}")
            payload = response.json()
            batch = sorted(payload.get("data", []), key=lambda item: item["index"])
            if len(batch) != len(batch_texts):
                raise ValueError("Embedding 返回数量与请求数量不一致")
            vectors.extend(item["embedding"] for item in batch)

    if any(len(vector) != settings.embedding_dimension for vector in vectors):
        raise ValueError("Embedding 返回维度与 TRIP_EMBEDDING_DIMENSION 不一致")
    return vectors


def rerank_candidates(query: str, candidates: list[dict], top_k: int) -> list[dict]:
    """使用可选的 Rerank 服务精排候选；未配置时保留 RRF 顺序并标注评分来源。"""
    settings = get_settings()
    if not candidates:
        return []
    if not (
        settings.reranker_base_url
        and settings.reranker_api_key
        and settings.reranker_model
    ):
        return [{**item, "score_source": "rrf"} for item in candidates[:top_k]]

    response = httpx.post(
        settings.reranker_base_url.rstrip("/") + "/rerank",
        headers={"Authorization": "Bearer " + settings.reranker_api_key},
        json={
            "model": settings.reranker_model,
            "query": query,
            "documents": [item["content"] for item in candidates],
            "top_n": min(settings.reranker_top_n, len(candidates)),
            "return_documents": False,
        },
        timeout=60,
    )
    if response.status_code != 200:
        raise ValueError(f"Reranker 服务返回 HTTP {response.status_code}")

    ranked: list[dict] = []
    for result in response.json().get("results", []):
        index = result.get("index")
        if not isinstance(index, int) or not 0 <= index < len(candidates):
            continue
        ranked.append(
            {
                **candidates[index],
                "score": float(result.get("relevance_score", 0)),
                "score_source": "reranker",
            }
        )
    return ranked[:top_k]


def vector_client():
    """按当前配置创建 Milvus 客户端；连接只在 Milvus 模式的任务内发生。"""
    from pymilvus import MilvusClient

    settings = get_settings()
    kwargs = {"uri": settings.milvus_uri}
    if settings.milvus_token:
        kwargs["token"] = settings.milvus_token
    return MilvusClient(**kwargs)


def collection_name() -> str:
    """将 Embedding 模型与维度编码进集合名，避免切换模型后误用旧向量。"""
    settings = get_settings()
    fingerprint = hashlib.sha256(
        f"{settings.embedding_model}:{settings.embedding_dimension}".encode()
    ).hexdigest()[:12]
    return "travel_hybrid_" + fingerprint


def step1_ensure_hybrid_collection(client) -> str:
    """创建带稠密向量和 Milvus 内置 BM25 稀疏向量的固定 Schema 与索引。"""
    from pymilvus import DataType, Function, FunctionType

    settings = get_settings()
    name = collection_name()
    if client.has_collection(name):
        return name

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field(
        "content",
        DataType.VARCHAR,
        max_length=65535,
        enable_analyzer=True,
        analyzer_params={"tokenizer": "jieba"},
    )
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=settings.embedding_dimension)
    schema.add_field("version_id", DataType.VARCHAR, max_length=64)
    schema.add_field("document_id", DataType.VARCHAR, max_length=64)
    schema.add_field("kb_id", DataType.VARCHAR, max_length=64)
    schema.add_function(
        Function(
            name="content_bm25",
            function_type=FunctionType.BM25,
            input_field_names=["content"],
            output_field_names=["sparse_vector"],
        )
    )

    indexes = client.prepare_index_params()
    indexes.add_index(
        field_name="dense_vector",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )
    indexes.add_index(
        field_name="sparse_vector",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="BM25",
        params={
            "inverted_index_algo": "DAAT_WAND",
            "bm25_k1": 1.2,
            "bm25_b": 0.75,
        },
    )
    client.create_collection(
        collection_name=name,
        schema=schema,
        index_params=indexes,
        consistency_level="Strong",
    )
    return name


def index_version(document: Document, version: DocumentVersion, chunks: list[Chunk]) -> None:
    """为一个待发布版本写入向量和原文；本地 BM25 模式直接使用数据库中的持久化片段。"""
    if get_settings().vector_backend != "milvus" or not chunks:
        return

    client = vector_client()
    name = step1_ensure_hybrid_collection(client)
    vectors = create_embeddings([chunk.content for chunk in chunks])
    entities = [
        {
            "chunk_id": chunk.id,
            "content": chunk.content,
            "dense_vector": vector,
            "version_id": version.id,
            "document_id": document.id,
            "kb_id": document.knowledge_base_id,
        }
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    client.upsert(collection_name=name, data=entities)
    client.flush(collection_name=name)


def step1_build_authorized_chunk_query(user, kb_id: str | None = None):
    """构造统一的可见性查询，确保停用、删除、历史版本和私有知识库在 SQL 层被拦截。"""
    query = (
        select(Chunk, DocumentVersion, Document, KnowledgeBase)
        .select_from(Chunk)
        .join(DocumentVersion, Chunk.version_id == DocumentVersion.id)
        .join(Document, DocumentVersion.document_id == Document.id)
        .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
        .where(
            Document.enabled.is_(True),
            Document.deleted.is_(False),
            Document.active_version_id == DocumentVersion.id,
            DocumentVersion.status == "READY",
            (KnowledgeBase.visibility == "public") | (KnowledgeBase.owner_id == user.id),
        )
    )
    if kb_id:
        query = query.where(KnowledgeBase.id == kb_id)
    return query


def step1_load_authorized_chunks(db, user, kb_id: str | None = None):
    """加载当前用户可访问且仍在有效期内的已发布片段，用于本地检索和引用复核。"""
    rows = list(db.execute(step1_build_authorized_chunk_query(user, kb_id)))
    today = date.today().isoformat()
    return [
        row
        for row in rows
        if not row[1].metadata_json.get("valid_until")
        or row[1].metadata_json["valid_until"] >= today
    ]


def step2_format_candidates(rows, fused_scores: dict[str, float]) -> list[dict]:
    """把数据库实体转换为可回溯引用，并按召回融合分数排序。"""
    candidates: list[dict] = []
    for chunk, version, document, knowledge_base in rows:
        if chunk.id not in fused_scores:
            continue
        candidates.append(
            {
                "chunk_id": chunk.id,
                "document_id": document.id,
                "version_id": version.id,
                "knowledge_base_id": knowledge_base.id,
                "filename": version.filename,
                "page": chunk.page,
                "heading": chunk.heading,
                "content": chunk.content,
                "score": fused_scores[chunk.id],
                "score_source": "rrf",
                "source_url": version.metadata_json.get("source_url", ""),
            }
        )
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def step2_search_local_bm25(rows, query: str) -> dict[str, float]:
    """开发模式下从已授权片段做 BM25 召回，并输出可与其他通道兼容的 RRF 分数。"""
    tokenized_documents = [tokenize(row[0].content) for row in rows]
    query_terms = tokenize(query)
    if not query_terms:
        return {}
    scores = BM25Okapi(tokenized_documents).get_scores(query_terms)
    matching_indexes = [
        index
        for index, words in enumerate(tokenized_documents)
        if set(words) & set(query_terms)
    ]
    lexical = sorted(
        matching_indexes,
        key=lambda index: float(scores[index]),
        reverse=True,
    )[:30]
    return {
        rows[index][0].id: 1 / (60 + rank)
        for rank, index in enumerate(lexical, 1)
    }


def step2_search_milvus_hybrid(rows, query: str) -> dict[str, float]:
    """使用 Milvus 内置 BM25 与稠密向量并行召回，再由 RRF 合并两个排名。"""
    from pymilvus import AnnSearchRequest, Function, FunctionType

    if not rows:
        return {}
    client = vector_client()
    name = step1_ensure_hybrid_collection(client)
    knowledge_base_ids = sorted({row[3].id for row in rows})
    allowed_chunk_ids = {row[0].id for row in rows}
    permission_filter = "kb_id in " + json.dumps(knowledge_base_ids)
    dense_vector = create_embeddings([query])[0]
    requests = [
        AnnSearchRequest(
            data=[query],
            anns_field="sparse_vector",
            param={"metric_type": "BM25"},
            limit=30,
            filter=permission_filter,
        ),
        AnnSearchRequest(
            data=[dense_vector],
            anns_field="dense_vector",
            param={"metric_type": "COSINE"},
            limit=30,
            filter=permission_filter,
        ),
    ]
    hits = client.hybrid_search(
        collection_name=name,
        reqs=requests,
        ranker=Function(
            name="hybrid_rrf",
            input_field_names=[],
            function_type=FunctionType.RERANK,
            params={"reranker": "rrf", "k": 60},
        ),
        limit=30,
        output_fields=["chunk_id", "version_id", "document_id", "kb_id"],
        consistency_level="Strong",
    )[0]
    return {
        hit["chunk_id"]: float(hit["distance"])
        for hit in hits
        if hit["chunk_id"] in allowed_chunk_ids
    }


def search_knowledge(db, user, query: str, kb_id: str | None = None, top_k: int = 8):
    """执行权限过滤、候选召回、候选实体还原和可选精排，返回可引用的证据片段。"""
    rows = step1_load_authorized_chunks(db, user, kb_id)
    if not rows or not query.strip():
        return []

    if get_settings().vector_backend == "milvus":
        fused_scores = step2_search_milvus_hybrid(rows, query)
    else:
        fused_scores = step2_search_local_bm25(rows, query)
    candidates = step2_format_candidates(rows, fused_scores)
    return rerank_candidates(query, candidates[:30], top_k)
