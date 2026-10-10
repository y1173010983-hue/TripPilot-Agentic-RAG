"""多轮对话合同：隔离、幂等、取消、真实图节点执行与持久历史。"""
from trippilot.worker import run_one
from trippilot.retrieval.module5_agent_workflow import step4_validate_time_and_budget


def test_session_isolation_idempotency_cancel(client, accounts):
    h = accounts["traveler"]
    sid = client.post("/api/v1/sessions", headers=h, json={}).json()["id"]
    body = {"session_id": sid, "client_message_id": "one", "content": "北京怎么玩"}
    a = client.post("/api/v1/chat", headers=h, json=body).json()
    b = client.post("/api/v1/chat", headers=h, json=body).json()
    assert a["run_id"] == b["run_id"] and b["duplicate"]
    assert client.get(f"/api/v1/sessions/{sid}/messages", headers=accounts["another"]).status_code == 404
    assert client.post("/api/v1/chat", headers=h, json={**body, "client_message_id": "two"}).status_code == 409
    client.post(f"/api/v1/chat/runs/{a['run_id']}/cancel", headers=h)
    run_one()
    assert client.get(f"/api/v1/chat/runs/{a['run_id']}", headers=h).json()["status"] == "cancelled"


def test_graph_history_and_missing_model(client, accounts, monkeypatch):
    h = accounts["traveler"]
    sid = client.post("/api/v1/sessions", headers=h, json={}).json()["id"]
    run = client.post("/api/v1/chat", headers=h, json={"session_id": sid, "client_message_id": "first", "content": "预算六千"}).json()
    run_one()
    result = client.get(f"/api/v1/chat/runs/{run['run_id']}", headers=h).json()
    assert result["status"] == "failed" and "未配置" in result["message"]["content"]
    from trippilot.retrieval.module1_model_gateway import ModelGateway
    monkeypatch.setattr(ModelGateway, "complete_json", lambda *a, **k: {"intent": "qa", "search_query": "预算", "requirement_updates": {"budget": 6000}})
    def stream(self, messages, callback):
        callback("你的预算是")
        callback("6000元。")
        return "你的预算是6000元。"
    monkeypatch.setattr(ModelGateway, "stream_answer", stream)
    run = client.post("/api/v1/chat", headers=h, json={"session_id": sid, "client_message_id": "second", "content": "你记得预算吗"}).json()
    run_one()
    result = client.get(f"/api/v1/chat/runs/{run['run_id']}", headers=h).json()
    assert result["status"] == "completed", result
    assert result["result"]["requirements"]["budget"] == 6000
    assert len(client.get(f"/api/v1/sessions/{sid}/messages", headers=h).json()) == 4
    assert client.get(f"/api/v1/chat/runs/{run['run_id']}/events", headers=h).status_code == 200


def test_budget_memory_respects_explicit_no_itinerary(client, accounts, monkeypatch):
    """即使路由模型误判，用户只让记住预算时也不能触发行程生成。"""
    from trippilot.retrieval.module1_model_gateway import ModelGateway

    calls = []

    def classify(self, messages, max_tokens=1800):
        calls.append(messages)
        return {"intent": "plan", "clarification": None, "requirement_updates": {"budget": 3000}}

    def answer(self, messages, callback):
        callback("已记住预算3000元。")
        return "已记住预算3000元。"

    monkeypatch.setattr(ModelGateway, "complete_json", classify)
    monkeypatch.setattr(ModelGateway, "stream_answer", answer)
    headers = accounts["traveler"]
    sid = client.post("/api/v1/sessions", headers=headers, json={}).json()["id"]
    run = client.post("/api/v1/chat", headers=headers, json={
        "session_id": sid, "client_message_id": "budget-only",
        "content": "预算3000元，请记住，暂时不要制定行程。",
    }).json()
    run_one()
    result = client.get(f"/api/v1/chat/runs/{run['run_id']}", headers=headers).json()
    assert result["status"] == "completed", result
    assert result["result"]["requirements"]["budget"] == 3000
    assert client.get(f"/api/v1/sessions/{sid}/plans", headers=headers).json() == []
    assert len(calls) == 1


def test_invalid_model_plan_has_user_friendly_error(client, accounts, monkeypatch):
    """模型返回不完整行程时给出可读提示，不把验证堆栈展示给用户。"""
    from trippilot.retrieval.module1_model_gateway import ModelGateway

    responses = iter([{"intent": "plan"}, {"title": "不完整行程", "activities": []}])
    monkeypatch.setattr(ModelGateway, "complete_json", lambda *args, **kwargs: next(responses))
    headers = accounts["traveler"]
    sid = client.post("/api/v1/sessions", headers=headers, json={}).json()["id"]
    run = client.post("/api/v1/chat", headers=headers, json={
        "session_id": sid, "client_message_id": "invalid-plan", "content": "请制定北京两日行程。",
    }).json()
    run_one()
    result = client.get(f"/api/v1/chat/runs/{run['run_id']}", headers=headers).json()
    assert result["status"] == "failed"
    assert "数据格式不完整" in result["message"]["content"]
    assert "DraftPlan" not in result["message"]["content"]
    assert "input_value" not in result["message"]["content"]


def test_plan_validation():
    result = step4_validate_time_and_budget({"plan": {"activities": [
        {"name": "A", "day": 1, "start": "10:00", "end": "12:00", "estimated_cost": 200},
        {"name": "B", "day": 1, "start": "11:00", "end": "13:00", "estimated_cost": 200}]},
        "requirements": {"budget": 300, "locked_activities": ["C"]}})
    assert len(result["issues"]) == 3
