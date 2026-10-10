"""覆盖 Milvus 3 自定义主键返回格式，以及召回结果的权限复核。"""

from types import SimpleNamespace

from pymilvus.client.search_result import Hit

from trippilot.retrieval import module2_knowledge_search as search


def test_custom_primary_key_preserves_fusion_score_and_filters_unauthorized(monkeypatch):
    """真实 SDK Hit 不保证存在 id 字段，且越权候选不得进入精排。"""
    hits = [
        Hit({"chunk_id": "allowed", "distance": 0.032, "entity": {}}, pk_name="chunk_id"),
        Hit({"chunk_id": "denied", "distance": 0.031, "entity": {}}, pk_name="chunk_id"),
    ]
    client = SimpleNamespace(hybrid_search=lambda **kwargs: [hits])
    monkeypatch.setattr(search, "vector_client", lambda: client)
    monkeypatch.setattr(search, "step1_ensure_hybrid_collection", lambda client: "test")
    monkeypatch.setattr(search, "create_embeddings", lambda texts: [[1.0, 0.0]])
    rows = [(SimpleNamespace(id="allowed"), None, None, SimpleNamespace(id="kb"))]

    assert search.step2_search_milvus_hybrid(rows, "旅游资料") == {"allowed": 0.032}
