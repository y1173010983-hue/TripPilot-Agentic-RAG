"""Run 30 hand-written TripPilot V4 acceptance cases on a remote adapter."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from llamafactory.chat import ChatModel

SYSTEM = """领域边界优先：只处理旅游需求及相关追问。非旅游知识、编程、作业、投资等问题必须选 out_of_scope；即使历史有旅游行程，也不能把当前无关问题当旅游问题。混合问题仅处理旅游部分。问候、感谢、询问助手能力属于 social。out_of_scope 时 tools/domains 为空数组、requirement_updates 为空对象、search_query 为空、clarification 为 null。只输出 JSON，字段 intent,domains,search_query,requirement_updates,clarification,tools。intent 必须为 out_of_scope/social/qa/explore/compare/plan/modify/format/booking。requirement_updates 只填本轮明确更新字段，允许 destination,departure,start_date,end_date,days,travelers,budget,budget_scope,pace,interests,diet,hotel_area,rooms,locked_activities。tools 字段为 name,city,query,origin,destination,date；工具名只能是 weather_query,poi_search,restaurant_search,hotel_search,route_query,ticket_search,hotel_inventory,intercity_search。pace 只能是 relaxed 或 compact；interests 和 locked_activities 必须是数组。"""

ALLOWED_INTENTS = {"out_of_scope", "social", "qa", "explore", "compare", "plan", "modify", "format", "booking"}
ALLOWED_UPDATES = {"destination", "departure", "start_date", "end_date", "days", "travelers", "budget", "budget_scope", "pace", "interests", "diet", "hotel_area", "rooms", "locked_activities"}
ALLOWED_TOOLS = {"weather_query", "poi_search", "restaurant_search", "hotel_search", "route_query", "ticket_search", "hotel_inventory", "intercity_search"}
TOP_KEYS = {"intent", "domains", "search_query", "requirement_updates", "clarification", "tools"}
TOOL_KEYS = {"name", "city", "query", "origin", "destination", "date"}

SZ = {"destination": "深圳", "days": 3, "travelers": 2, "budget": 3000, "hotel_area": "福田中心区"}
CD = {"destination": "成都", "days": 4, "travelers": 3, "budget": 6000, "hotel_area": "春熙路"}
HZ = {"destination": "杭州", "days": 3, "travelers": 3, "budget": 4000, "hotel_area": "西湖东侧"}


def case(name, saved, user, intent, updates=None, tools=(), clarification=False):
    return {"name": name, "saved": saved, "user": user, "intent": intent,
            "updates": updates or {}, "tools": set(tools), "clarification": clarification}


CASES = [
    case("老人慢游", HZ, "爸妈一起去，麻烦每天别排太紧。", "modify", {"pace": "relaxed"}),
    case("快速游玩", SZ, "时间比较赶，三天尽量多看几个地方。", "modify", {"pace": "compact"}),
    case("预算不变", SZ, "花费按原来的三千控制，不需要改预算。", "qa"),
    case("人数不变", CD, "还是两大一小，总人数没变化。", "qa"),
    case("酒店不变", CD, "住宿继续放在春熙路一带。", "qa"),
    case("酒店换区", SZ, "晚上想逛海边，住后海那边更合适。", "modify", {"hotel_area": "后海"}, {"hotel_search"}),
    case("兴趣数组", CD, "孩子最期待熊猫和博物馆。", "modify", {"interests": ["熊猫", "博物馆"]}, {"poi_search"}),
    case("饮食限制", CD, "同行有人不能吃辣，餐厅按这个要求找。", "modify", {"diet": "不吃辣"}, {"restaurant_search"}),
    case("锁定活动", SZ, "世界之窗和欢乐谷一定要保留。", "modify", {"locked_activities": ["世界之窗", "欢乐谷"]}, {"poi_search"}),
    case("多人多房", SZ, "现在四个人同行，需要两间房。", "modify", {"travelers": 4, "rooms": 2}, {"hotel_search"}),
    case("日期范围", HZ, "十月一号出发，四号回程。", "modify", {"start_date": "2026-10-01", "end_date": "2026-10-04", "days": 4}),
    case("目的地切换", SZ, "深圳先不去了，改成杭州玩四天。", "modify", {"destination": "杭州", "days": 4}),
    case("天气", SZ, "明天要不要带伞？", "qa", tools={"weather_query"}),
    case("路线", CD, "从双流机场去春熙路，公共交通怎么坐？", "qa", tools={"route_query"}),
    case("景点探索", HZ, "杭州有什么适合带长辈去的室内景点？", "explore", tools={"poi_search"}),
    case("美食探索", SZ, "福田附近有哪些清淡一点的本地菜？", "qa", tools={"restaurant_search"}),
    case("酒店比较", CD, "春熙路和宽窄巷子，住哪里出行方便？", "compare", tools={"hotel_search"}),
    case("预订引导", SZ, "想订宝安中心附近的房间。", "booking", tools={"hotel_inventory"}),
    case("不明确景点", HZ, "把前面说的第二个景点删掉。", "modify", clarification=True),
    case("不明确日期", SZ, "第二天的安排再轻松些。", "modify", {"pace": "relaxed"}),
    case("仅记忆偏好", SZ, "请记住我喜欢海边和夜景，暂时不用做路线。", "qa", {"interests": ["海边", "夜景"]}),
    case("格式整理", CD, "把已有行程整理成按天的表格。", "format"),
    case("问候", SZ, "你好呀。", "social"),
    case("无关编程", SZ, "FastAPI 的依赖注入怎么用？", "out_of_scope"),
    case("无关金融", HZ, "帮我分析一下明天的股票走势。", "out_of_scope"),
    case("混合请求", SZ, "别讲 Python，直接告诉我深圳三天怎么安排。", "plan"),
    case("酒店替换不改区域", CD, "酒店换个评价好的，但区域仍放春熙路。", "modify", tools={"hotel_search"}),
    case("预算提升", HZ, "预算提高到六千，总预算口径不变。", "modify", {"budget": 6000}),
    case("交通偏好", SZ, "不想打车，优先地铁和步行。", "modify", {"pace": "relaxed"}),
    case("多条件", CD, "四个人去，安排熊猫基地，饮食清淡，行程别太累。", "modify", {"travelers": 4, "interests": ["熊猫基地"], "diet": "清淡", "pace": "relaxed"}, {"poi_search", "restaurant_search"}),
]


def validate(actual, expected):
    errors = []
    if set(actual) != TOP_KEYS:
        errors.append("顶层字段不符合协议")
    if actual.get("intent") not in ALLOWED_INTENTS:
        errors.append("非法 intent")
    updates = actual.get("requirement_updates", {})
    if set(updates) - ALLOWED_UPDATES:
        errors.append("存在非法需求字段")
    if actual.get("intent") != expected["intent"]:
        errors.append(f"intent 应为 {expected['intent']}")
    if updates != expected["updates"]:
        errors.append("需求更新字段或值不一致")
    actual_tool_names = set()
    for tool in actual.get("tools", []):
        if set(tool) != TOOL_KEYS:
            errors.append("工具字段不完整")
        name = tool.get("name")
        actual_tool_names.add(name)
        if name not in ALLOWED_TOOLS:
            errors.append("工具不在白名单")
    if not expected["tools"].issubset(actual_tool_names):
        errors.append("缺少预期工具")
    if actual.get("intent") == "out_of_scope" and (actual.get("domains") or updates or actual.get("search_query") or actual.get("tools")):
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
    chat = ChatModel({"model_name_or_path": args.model, "adapter_name_or_path": args.adapter,
                      "template": "qwen3_nothink", "infer_backend": "huggingface", "trust_remote_code": True})
    results = []
    for index, expected in enumerate(CASES, 1):
        user = "当前已保存需求：" + json.dumps(expected["saved"], ensure_ascii=False) + "。本轮用户：" + expected["user"]
        raw = chat.chat([{"role": "user", "content": user}], system=SYSTEM, do_sample=False, max_new_tokens=512)[0].response_text
        try:
            actual = json.loads(raw)
            errors = validate(actual, expected)
        except json.JSONDecodeError:
            actual, errors = None, ["模型输出不是合法 JSON"]
        results.append({"index": index, "name": expected["name"], "passed": not errors, "errors": errors, "raw": raw, "actual": actual})
        print(f"{index:02d}. {expected['name']}：{'通过' if not errors else '失败'}")
    passed = sum(item["passed"] for item in results)
    Path(args.report).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"验收汇总：{passed}/{len(results)}，通过率 {passed / len(results):.2%}")
    print(f"详细报告：{args.report}")


if __name__ == "__main__":
    main()
