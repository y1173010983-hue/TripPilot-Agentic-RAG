"""Protocol-V1 acceptance: run 30 fixed cases against a trained adapter."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SYSTEM = """你是 TripPilot 旅游对话路由器。只输出 JSON，顶层字段必须且只能是 intent、domains、search_query、requirement_updates、clarification、tools。
intent 必须且只能为 out_of_scope、social、qa、explore、compare、plan、modify、format、booking。
domains 只能包含 attraction、hotel、food、transportation、itinerary、weather、budget、travelers、dates、general。
requirement_updates 只填本轮用户明确更新或明确要求记住的字段，允许 destination、departure、start_date、end_date、days、travelers、budget、budget_scope、pace、interests、diet、hotel_area、rooms、locked_activities。
pace 只能是 relaxed 或 compact；interests 和 locked_activities 必须是字符串数组；diet 必须是字符串。
tools 最多 4 项，每项必须包含 name、city、query、origin、destination、date；工具名只能是 weather_query、poi_search、restaurant_search、hotel_search、route_query、ticket_search、hotel_inventory、intercity_search。
out_of_scope 时 tools/domains 为空数组、requirement_updates 为空对象、search_query 为空、clarification 为 null。
用户明确保持不变的历史字段不得重复写入 requirement_updates；仅要求记住偏好且不要行程时 intent 选 qa；换酒店但区域不变时不得更新 hotel_area。"""

TOP_KEYS = {"intent", "domains", "search_query", "requirement_updates", "clarification", "tools"}
ALLOWED_INTENTS = {"out_of_scope", "social", "qa", "explore", "compare", "plan", "modify", "format", "booking"}
ALLOWED_DOMAINS = {"attraction", "hotel", "food", "transportation", "itinerary", "weather", "budget", "travelers", "dates", "general"}
ALLOWED_UPDATES = {
    "destination", "departure", "start_date", "end_date", "days", "travelers",
    "budget", "budget_scope", "pace", "interests", "diet", "hotel_area", "rooms", "locked_activities",
}
ALLOWED_TOOLS = {
    "weather_query", "poi_search", "restaurant_search", "hotel_search", "route_query",
    "ticket_search", "hotel_inventory", "intercity_search",
}
TOOL_KEYS = {"name", "city", "query", "origin", "destination", "date"}

SZ = {"destination": "深圳", "days": 3, "travelers": 2, "budget": 3000, "hotel_area": "福田中心区"}
CD = {"destination": "成都", "days": 4, "travelers": 3, "budget": 6000, "hotel_area": "春熙路"}
HZ = {"destination": "杭州", "days": 3, "travelers": 3, "budget": 4000, "hotel_area": "西湖东侧"}

# name, saved, user, intent, domains, updates, expected tool names, needs clarification
CASES = [
    ("老人慢游", HZ, "爸妈一起去，每天别排太紧。", "modify", ("itinerary",), {"pace": "relaxed"}, (), False),
    ("快速游玩", SZ, "时间比较赶，三天尽量多看几个地方。", "modify", ("itinerary",), {"pace": "compact"}, (), False),
    ("预算不变", SZ, "花费按原来的三千控制，不需要改预算。", "qa", ("budget",), {}, (), False),
    ("人数不变", CD, "还是两大一小，总人数没变化。", "qa", ("travelers",), {}, (), False),
    ("酒店不变", CD, "住宿继续放在春熙路一带。", "qa", ("hotel",), {}, (), False),
    ("酒店换区", SZ, "晚上想逛海边，住后海那边更合适。", "modify", ("hotel",), {"hotel_area": "后海"}, ("hotel_search",), False),
    ("兴趣数组", CD, "孩子最期待熊猫和博物馆。", "modify", ("attraction",), {"interests": ["熊猫", "博物馆"]}, ("poi_search",), False),
    ("饮食限制", CD, "同行有人不能吃辣，餐厅按这个要求找。", "modify", ("food",), {"diet": "不吃辣"}, ("restaurant_search",), False),
    ("锁定活动", SZ, "世界之窗和欢乐谷一定要保留。", "modify", ("attraction",), {"locked_activities": ["世界之窗", "欢乐谷"]}, (), False),
    ("多人多房", SZ, "现在四个人同行，需要两间房。", "modify", ("travelers", "hotel"), {"travelers": 4, "rooms": 2}, ("hotel_search",), False),
    ("日期范围", HZ, "十月一号出发，四号回程。", "modify", ("itinerary", "dates"), {"start_date": "2026-10-01", "end_date": "2026-10-04", "days": 4}, (), False),
    ("目的地切换", SZ, "深圳先不去了，改成杭州玩四天。", "modify", ("itinerary", "dates"), {"destination": "杭州", "days": 4}, (), False),
    ("天气", SZ, "明天要不要带伞？", "qa", ("weather",), {}, ("weather_query",), False),
    ("路线", CD, "从双流机场去春熙路，公共交通怎么坐？", "qa", ("transportation",), {}, ("route_query",), False),
    ("景点探索", HZ, "杭州有什么适合带长辈去的室内景点？", "explore", ("attraction",), {}, ("poi_search",), False),
    ("美食探索", SZ, "福田附近有哪些清淡一点的本地菜？", "explore", ("food",), {}, ("restaurant_search",), False),
    ("酒店比较", CD, "春熙路和宽窄巷子，住哪里出行方便？", "compare", ("hotel",), {}, ("hotel_search",), False),
    ("预订引导", SZ, "想订宝安中心附近的房间。", "booking", ("hotel",), {"hotel_area": "宝安中心"}, ("hotel_inventory",), True),
    ("不明确景点", HZ, "把前面说的第二个景点删掉。", "modify", ("attraction",), {}, (), True),
    ("不明确日期", SZ, "第二天的安排再轻松些。", "modify", ("itinerary",), {"pace": "relaxed"}, (), False),
    ("仅记忆偏好", SZ, "请记住我喜欢海边和夜景，暂时不用做路线。", "qa", ("attraction",), {"interests": ["海边", "夜景"]}, (), False),
    ("格式整理", CD, "把已有行程整理成按天的表格。", "format", ("itinerary",), {}, (), False),
    ("问候", SZ, "你好呀。", "social", (), {}, (), False),
    ("无关编程", SZ, "FastAPI 的依赖注入怎么用？", "out_of_scope", (), {}, (), False),
    ("无关金融", HZ, "帮我分析一下明天的股票走势。", "out_of_scope", (), {}, (), False),
    ("混合请求", SZ, "别讲 Python，直接告诉我深圳三天怎么安排。", "plan", ("itinerary",), {}, (), False),
    ("酒店替换不改区域", CD, "酒店换个评价好的，但区域仍放春熙路。", "modify", ("hotel",), {}, ("hotel_search",), False),
    ("预算提升", HZ, "预算提高到六千，总预算口径不变。", "modify", ("budget",), {"budget": 6000}, (), False),
    ("交通偏好", SZ, "不想打车，优先地铁和步行。", "modify", ("itinerary",), {"pace": "relaxed"}, (), False),
    ("多条件", CD, "四个人去，安排熊猫基地，饮食清淡，行程别太累。", "modify", ("travelers", "attraction", "food", "itinerary"), {"travelers": 4, "interests": ["熊猫基地"], "diet": "清淡", "pace": "relaxed"}, ("poi_search", "restaurant_search"), False),
]


def validate(actual, expected):
    errors = []
    if not isinstance(actual, dict) or set(actual) != TOP_KEYS:
        errors.append("顶层字段不符合协议")
        return errors
    if actual.get("intent") not in ALLOWED_INTENTS:
        errors.append("非法 intent")
    if not isinstance(actual.get("domains"), list) or set(actual.get("domains")) - ALLOWED_DOMAINS:
        errors.append("非法 domains")
    if set(actual.get("requirement_updates", {})) - ALLOWED_UPDATES:
        errors.append("存在非法需求字段")
    if actual.get("intent") != expected["intent"]:
        errors.append(f"intent 应为 {expected['intent']}")
    if set(actual.get("domains") or []) != set(expected["domains"]):
        errors.append("domains 不一致")
    if actual.get("requirement_updates") != expected["updates"]:
        errors.append("需求更新字段或值不一致")
    actual_tools = []
    for item in actual.get("tools", []):
        if not isinstance(item, dict) or set(item) != TOOL_KEYS:
            errors.append("工具字段不完整")
            continue
        name = item.get("name")
        actual_tools.append(name)
        if name not in ALLOWED_TOOLS:
            errors.append("工具不在白名单")
        if name == "weather_query" and not (item.get("city") or "").strip():
            errors.append("天气查询缺少城市")
        if name in {"poi_search", "restaurant_search", "hotel_search", "hotel_inventory"} and not (item.get("query") or "").strip():
            errors.append(f"{name} 缺少关键词")
        if name == "route_query" and not ((item.get("origin") or "").strip() and (item.get("destination") or "").strip()):
            errors.append("路线查询缺少起点或终点")
    if not expected["tools"].issubset(set(actual_tools)):
        errors.append("缺少预期工具")
    if actual.get("intent") == "out_of_scope" and (actual.get("domains") or actual.get("requirement_updates") or actual.get("search_query") or actual.get("tools")):
        errors.append("无关问题污染上下文")
    if expected["clarification"] and not actual.get("clarification"):
        errors.append("应该澄清但没有 clarification")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    from llamafactory.chat import ChatModel
    chat = ChatModel({
        "model_name_or_path": args.model,
        "adapter_name_or_path": args.adapter,
        "template": "qwen3_nothink",
        "infer_backend": "huggingface",
        "trust_remote_code": True,
    })
    results = []
    for index, (name, saved, user_text, intent, domains, updates, tools, clarification) in enumerate(CASES, 1):
        expected = {
            "intent": intent,
            "domains": domains,
            "updates": updates,
            "tools": set(tools),
            "clarification": clarification,
        }
        user = "当前已保存需求：" + json.dumps(saved, ensure_ascii=False) + "。本轮用户：" + user_text
        raw = chat.chat([{"role": "user", "content": user}], system=SYSTEM, do_sample=False, max_new_tokens=512)[0].response_text
        try:
            actual = json.loads(raw)
            errors = validate(actual, expected)
        except json.JSONDecodeError:
            actual, errors = None, ["模型输出不是合法 JSON"]
        results.append({"index": index, "name": name, "passed": not errors, "errors": errors, "raw": raw, "actual": actual})
        print(f"{index:02d}. {name}：{'通过' if not errors else '失败'}")
    passed = sum(item["passed"] for item in results)
    Path(args.report).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"验收汇总：{passed}/{len(results)}，通过率 {passed / len(results):.2%}")
    print(f"详细报告：{args.report}")


if __name__ == "__main__":
    main()
