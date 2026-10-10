from trippilot.retrieval import module5_agent_workflow as workflow


def test_weather_only_request_skips_knowledge_retrieval(monkeypatch):
    monkeypatch.setattr(workflow, "require_running", lambda _run_id: None)
    monkeypatch.setattr(
        workflow,
        "search_knowledge",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应进入知识检索")),
    )
    monkeypatch.setattr(workflow, "execute_travel_tool", lambda request: {"tool": request.name, "status": "ok"})
    result = workflow.step2_retrieve_evidence_and_tools(
        {
            "run_id": "run-1",
            "session_id": "session-1",
            "decision": {
                "intent": "qa",
                "domains": ["weather"],
                "search_query": "深圳明天天气",
                "requirement_updates": {},
                "clarification": None,
                "tools": [{"name": "weather_query", "city": "深圳"}],
            },
            "context": {"history": [{"role": "user", "content": "深圳明天天气怎么样"}]},
            "requirements": {},
        }
    )
    assert result["evidence"] == []
    assert result["tool_results"] == [{"tool": "weather_query", "status": "ok"}]


def test_plan_answer_is_formatted_without_second_model_call():
    answer = workflow.step5_format_plan_answer(
        {
            "title": "北京两日历史文化游",
            "activities": [
                {
                    "name": "故宫博物院",
                    "day": 1,
                    "start": "09:00",
                    "end": "12:00",
                    "estimated_cost": 60.0,
                    "note": "门票和开放时间请出发前核实。",
                }
            ],
            "unknowns": ["实时余票待核实"],
        },
        [],
        [],
    )
    assert "北京两日历史文化游" in answer
    assert "故宫博物院" in answer
    assert "约 ¥60" in answer


def test_two_day_plan_uses_bounded_model_output(monkeypatch):
    monkeypatch.setattr(workflow, "require_running", lambda _run_id: None)

    class FakeGateway:
        def complete_json(self, _messages, max_tokens):
            assert max_tokens == 1800
            return {
                "title": "测试行程",
                "activities": [
                    {"name": "景点", "day": 1, "start": "09:00", "end": "10:00"}
                ],
            }

    monkeypatch.setattr(workflow, "ModelGateway", FakeGateway)
    result = workflow.step3_build_or_revise_plan(
        {
            "run_id": "run-1",
            "decision": {"intent": "plan", "clarification": None},
            "requirements": {"days": 2},
            "context": {},
            "evidence": [],
            "tool_results": [],
            "plan": None,
            "issues": [],
            "revision": 0,
        }
    )
    assert result["revision"] == 1
