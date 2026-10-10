"""验证领域拦截不调用检索和回答模型。"""
from trippilot.retrieval import module5_agent_workflow as workflow


def test_scope_rejection(monkeypatch):
    monkeypatch.setattr(workflow, "require_running", lambda _: None)
    state = {"run_id": "test", "decision": {"intent": "out_of_scope"}}
    assert workflow.step2_retrieve_evidence_and_tools(state) == {"evidence": [], "tool_results": []}
    chunks = []
    result = workflow.make_answer_node(chunks.append)(state)
    assert "无法回答与旅游无关" in result["answer"]
    assert chunks == [result["answer"]]
    assert result["plan"] is None
