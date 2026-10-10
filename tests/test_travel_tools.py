"""旅游工具合同测试：不调用外部接口，不消耗供应商额度。"""
from types import SimpleNamespace

from trippilot.retrieval import module4_travel_tools as travel


def test_weather_resolves_city_before_request(monkeypatch):
    monkeypatch.setattr(travel, "get_settings", lambda: SimpleNamespace(amap_key="test"))
    calls = []

    def fake_call(path, params):
        calls.append((path, params))
        if path == "config/district":
            return {"districts": [{"adcode": "440300"}]}, ""
        return {"lives": [{"city": "深圳市", "weather": "晴"}]}, ""

    monkeypatch.setattr(travel, "step2_call_amap", fake_call)
    result = travel.execute_travel_tool(travel.ToolRequest(name="weather_query", city="深圳"))
    assert result["status"] == "success"
    assert calls[1][1] == {"city": "440300", "extensions": "base"}


def test_hotel_search_limits_city(monkeypatch):
    monkeypatch.setattr(travel, "get_settings", lambda: SimpleNamespace(amap_key="test"))
    calls = []

    def fake_call(path, params):
        calls.append(params)
        return {"pois": []}, ""

    monkeypatch.setattr(travel, "step2_call_amap", fake_call)
    travel.execute_travel_tool(travel.ToolRequest(name="hotel_search", city="深圳", query="宝安中心酒店"))
    assert calls[0]["types"] == "100000"
    assert calls[0]["citylimit"] == "true"
    result = travel.execute_travel_tool(travel.ToolRequest(name="hotel_inventory", city="深圳"))
    assert result["status"] == "unavailable"
