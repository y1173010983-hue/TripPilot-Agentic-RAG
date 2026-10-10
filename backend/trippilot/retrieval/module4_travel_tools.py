"""旅游工具模块：统一参数校验、实时来源时间戳和未配置状态，禁止模型虚构动态数据。"""

from __future__ import annotations

from datetime import date, datetime, timezone
import re
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings


class ToolRequest(BaseModel):
    """Agent 可请求的旅游工具参数；路线起终点可以是地址、地名或经纬度。"""

    model_config = ConfigDict(extra="forbid")
    name: Literal["weather_query", "poi_search", "restaurant_search", "hotel_search", "route_query", "ticket_search", "hotel_inventory", "intercity_search"]
    city: str = Field(default="", max_length=100)
    query: str = Field(default="", max_length=200)
    origin: str = Field(default="", max_length=100)
    destination: str = Field(default="", max_length=100)
    date: str = Field(default="", max_length=20)


def step1_build_unavailable_result(tool_name: str, reason: str = "") -> dict:
    """创建结构稳定的不可用结果，让回答层能解释缺失能力而不会编造结果。"""
    return {
        "tool": tool_name,
        "status": "unavailable",
        "data": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "高德开放平台",
        **({"reason": reason} if reason else {}),
    }


def step2_call_amap(path: str, params: dict) -> tuple[dict | None, str]:
    """调用高德 Web 服务并把网络、权限和业务错误统一为可展示原因。"""
    settings = get_settings()
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(
                "https://restapi.amap.com/v3/" + path,
                params={"key": settings.amap_key, **params},
            )
        if response.status_code != 200:
            return None, f"地图服务返回 HTTP {response.status_code}"
        payload = response.json()
        if payload.get("status") != "1":
            detail = payload.get("info") or "Key 权限或配额不足"
            return None, f"地图服务拒绝请求：{detail}"
        return payload, ""
    except (httpx.HTTPError, ValueError):
        return None, "地图服务超时或网络不可用"


def step3_resolve_route_location(place: str, city: str) -> tuple[str, str]:
    """保留合法经纬度；否则先把地址或地名编码为高德坐标。"""
    if re.fullmatch(r"-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?", place.strip()):
        return place.strip(), ""
    payload, reason = step2_call_amap(
        "geocode/geo",
        {"address": place, **({"city": city} if city else {})},
    )
    geocodes = payload.get("geocodes", []) if payload else []
    if not geocodes or not geocodes[0].get("location"):
        return "", reason or f"无法解析地点：{place}"
    return geocodes[0]["location"], ""


def execute_travel_tool(tool: ToolRequest) -> dict:
    """按白名单执行旅游查询；所有成功结果附真实来源和获取时间。"""
    result = step1_build_unavailable_result(tool.name)
    if tool.name in {"ticket_search", "hotel_inventory", "intercity_search"}:
        return {
            **result,
            "reason": "尚未配置票务、酒店库存或城际交通供应商，只能提供官方查询入口",
        }
    if tool.name not in {
        "weather_query",
        "poi_search",
        "restaurant_search",
        "hotel_search",
        "route_query",
    }:
        return {**result, "reason": "不支持该工具"}
    if not get_settings().amap_key:
        return {**result, "reason": "管理员尚未配置高德 Web 服务 API Key"}

    if tool.name == "weather_query":
        if not tool.city:
            return {**result, "reason": "天气查询缺少城市"}
        if tool.date:
            try:
                days_from_today = (date.fromisoformat(tool.date) - date.today()).days
            except ValueError:
                return {**result, "reason": "日期格式应为 YYYY-MM-DD"}
            if not 0 <= days_from_today <= 3:
                return {**result, "reason": "日期超出高德天气预报支持的近期范围"}
        # 天气接口要求行政区编码，不能直接传用户输入的中文城市名。
        city_code = tool.city.strip()
        if not re.fullmatch(r"\d{6}", city_code):
            district, reason = step2_call_amap(
                "config/district", {"keywords": city_code, "subdistrict": "0"}
            )
            matches = district.get("districts", []) if district else []
            if len(matches) != 1 or not matches[0].get("adcode"):
                return {**result, "reason": reason or "城市不明确，请补充省份或区县"}
            city_code = matches[0]["adcode"]
        payload, reason = step2_call_amap(
            "weather/weatherInfo",
            {"city": city_code, "extensions": "all" if tool.date else "base"},
        )
        if payload and not (payload.get("forecasts") or payload.get("lives")):
            return {**result, "reason": "该地区暂无天气数据"}
    elif tool.name == "route_query":
        if not tool.origin or not tool.destination:
            return {**result, "reason": "路线查询缺少起点或终点"}
        origin, origin_error = step3_resolve_route_location(tool.origin, tool.city)
        destination, destination_error = step3_resolve_route_location(
            tool.destination, tool.city
        )
        if origin_error or destination_error:
            return {**result, "reason": origin_error or destination_error}
        payload, reason = step2_call_amap(
            "direction/walking",
            {"origin": origin, "destination": destination},
        )
    else:
        keywords = tool.query or tool.destination
        if not keywords:
            return {**result, "reason": "地点查询缺少关键词"}
        params = {
            "city": tool.city,
            "keywords": keywords,
            "offset": "5",
            "extensions": "base",
        }
        if tool.name == "restaurant_search":
            params["types"] = "050000"
        elif tool.name == "hotel_search":
            params["types"] = "100000"
            if not tool.city:
                return {**result, "reason": "请提供酒店所在城市"}
            params["citylimit"] = "true"
        payload, reason = step2_call_amap("place/text", params)

    if not payload:
        return {**result, "reason": reason}
    return {
        **result,
        "status": "success",
        "data": payload,
        "note": "POI 结果不代表实时营业或实时价格；当前路线工具返回步行路线。",
    }
