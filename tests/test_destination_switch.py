import json

from trippilot.retrieval.module3_context_memory import (
    step3_is_destination_switch,
    step4_rebuild_requirements_after_destination_switch,
)


def test_destination_switch_detects_replacement_not_same_destination():
    assert step3_is_destination_switch({"destination": "广州"}, {"destination": "西藏"})
    assert not step3_is_destination_switch({"destination": "广州"}, {"destination": "广州"})
    assert not step3_is_destination_switch({"destination": "广州"}, {"days": 5})


def test_destination_switch_clears_location_bound_requirements_and_keeps_reusable_values():
    previous = {
        "destination": "广州",
        "departure": "深圳",
        "days": 3,
        "budget": 5000,
        "travelers": 2,
        "hotel_area": "天河区",
        "locked_activities": ["广州塔"],
        "interests": ["美食"],
    }
    result = step4_rebuild_requirements_after_destination_switch(previous, {"destination": "西藏"})

    assert result == {
        "destination": "西藏",
        "departure": "深圳",
        "budget": 5000,
        "travelers": 2,
        "interests": ["美食"],
    }


def test_destination_switch_keeps_new_destination_specific_values_from_current_message():
    previous = {"destination": "广州", "hotel_area": "天河区", "days": 3}
    result = step4_rebuild_requirements_after_destination_switch(
        previous,
        {"destination": "西藏", "days": 7, "hotel_area": "拉萨市区", "locked_activities": ["布达拉宫"]},
    )

    assert result["destination"] == "西藏"
    assert result["days"] == 7
    assert result["hotel_area"] == "拉萨市区"
    assert result["locked_activities"] == ["布达拉宫"]


def test_destination_switch_clears_old_plan_context_and_forces_days_clarification(client, accounts, monkeypatch):
    from trippilot.database import session_factory
    from trippilot.models import ChatSession, TripPlan
    from trippilot.retrieval.module1_model_gateway import ModelGateway
    from trippilot.worker import run_one

    headers = accounts["traveler"]
    sid = client.post("/api/v1/sessions", headers=headers, json={}).json()["id"]
    with session_factory() as db:
        chat = db.get(ChatSession, sid)
        chat.requirements = {
            "destination": "广州",
            "days": 3,
            "hotel_area": "天河区",
            "locked_activities": ["广州塔"],
            "budget": 5000,
        }
        db.add(TripPlan(session_id=sid, version=1, content={"title": "广州三日游", "activities": []}))
        db.commit()

    monkeypatch.setattr(
        ModelGateway,
        "complete_json",
        lambda *args, **kwargs: {"intent": "modify", "requirement_updates": {"destination": "西藏"}},
    )
    observed = []

    def answer(self, messages, callback):
        observed.append(messages)
        callback("请补充信息")
        return "请补充信息"

    monkeypatch.setattr(ModelGateway, "stream_answer", answer)
    run = client.post("/api/v1/chat", headers=headers, json={
        "session_id": sid,
        "client_message_id": "switch-destination",
        "content": "不去广州了，改去西藏",
    }).json()
    run_one()

    result = client.get(f"/api/v1/chat/runs/{run['run_id']}", headers=headers).json()
    assert result["status"] == "completed", result
    assert result["result"]["requirements"] == {"destination": "西藏", "budget": 5000}
    state = json.loads(observed[0][1]["content"])
    assert state["context"]["previous_plan"] is None
    assert "计划游玩几天" in state["decision"]["clarification"]
