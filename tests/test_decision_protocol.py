"""Regression tests for the LLM-to-memory protocol gate."""

from trippilot.retrieval.module5_agent_workflow import step0_normalize_and_validate_decision


def step1_raw_decision(**overrides):
    data = {
        "intent": "modify",
        "domains": ["itinerary"],
        "search_query": "",
        "requirement_updates": {},
        "clarification": None,
        "tools": [],
    }
    data.update(overrides)
    return data


def test_protocol_normalizes_pace_and_string_list():
    decision = step0_normalize_and_validate_decision(
        step1_raw_decision(requirement_updates={"pace": "轻松", "interests": "熊猫"}),
        {},
    )
    assert decision.requirement_updates == {"pace": "relaxed", "interests": ["熊猫"]}


def test_protocol_drops_unchanged_fields_before_memory_merge():
    decision = step0_normalize_and_validate_decision(
        step1_raw_decision(requirement_updates={"travelers": 3, "hotel_area": "春熙路"}),
        {"travelers": 3, "hotel_area": "春熙路"},
    )
    assert decision.requirement_updates == {}


def test_protocol_rejects_unknown_top_level_field_and_tool_name():
    unknown_field = step0_normalize_and_validate_decision(
        {**step1_raw_decision(), "entities": {}}, {},
    )
    assert unknown_field.intent == "qa"

    unknown_tool = step0_normalize_and_validate_decision(
        step1_raw_decision(tools=[{
            "name": "attraction_search", "city": "成都", "query": "熊猫",
            "origin": "", "destination": "", "date": "",
        }]),
        {},
    )
    assert unknown_tool.intent == "qa"


def test_protocol_rejects_empty_query_for_poi_search():
    decision = step0_normalize_and_validate_decision(
        step1_raw_decision(tools=[{
            "name": "poi_search", "city": "成都", "query": "",
            "origin": "", "destination": "", "date": "",
        }]),
        {},
    )
    assert decision.intent == "qa"


def test_protocol_normalizes_diet_list_and_uses_dates_as_day_source_of_truth():
    decision = step0_normalize_and_validate_decision(
        step1_raw_decision(requirement_updates={
            "diet": ["不吃辣"],
            "start_date": "2026-10-01",
            "end_date": "2026-10-04",
            "days": 3,
        }),
        {},
    )
    assert decision.requirement_updates["diet"] == "不吃辣"
    assert decision.requirement_updates["days"] == 4


def test_protocol_rejects_past_dates_and_empty_inventory_query():
    past_date = step0_normalize_and_validate_decision(
        step1_raw_decision(requirement_updates={"start_date": "2024-10-01", "end_date": "2024-10-04"}),
        {},
    )
    assert past_date.intent == "qa"
    empty_inventory = step0_normalize_and_validate_decision(
        step1_raw_decision(tools=[{
            "name": "hotel_inventory", "city": "深圳", "query": "",
            "origin": "", "destination": "", "date": "",
        }]),
        {},
    )
    assert empty_inventory.intent == "qa"
