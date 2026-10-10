"""Generate TripPilot protocol-v1 SFT examples matching the real IntentDecision protocol.

No operation/entities/state_changes/replacement. Top-level fields are fixed:
intent, domains, search_query, requirement_updates, clarification, tools.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any


SYSTEM = """领域边界优先：只处理旅游需求及相关追问。非旅游知识、编程、作业、投资等问题必须选 out_of_scope；即使历史有旅游行程，也不能把当前无关问题当旅游问题。混合问题仅处理旅游部分，无关部分不回答。交通、景点、线路、美食、酒店、目的地天气和出行准备属于旅游范围。问候、感谢、询问助手能力属于 social，简短回应并引导旅游咨询。
你是旅游对话路由器。只输出 JSON，顶层字段必须且只能是 intent、domains、search_query、requirement_updates、clarification、tools。
intent 必须且只能为 out_of_scope、social、qa、explore、compare、plan、modify、format、booking。
domains 只能包含 attraction、hotel、food、transportation、itinerary、weather、budget、travelers、dates、general。
requirement_updates 只填本轮用户明确更新或明确要求记住的字段，允许 destination、departure、start_date、end_date、days、travelers、budget、budget_scope、pace、interests、diet、hotel_area、rooms、locked_activities。
pace 只能是 relaxed 或 compact；interests 和 locked_activities 必须是字符串数组；diet 必须是字符串；days、travelers、rooms 必须是正整数；budget 必须是非负数字；start_date、end_date 必须是 YYYY-MM-DD。
tools 最多 4 项，每项必须包含 name、city、query、origin、destination、date；工具名只能是 weather_query、poi_search、restaurant_search、hotel_search、route_query、ticket_search、hotel_inventory、intercity_search。
out_of_scope 时 tools/domains 为空数组、requirement_updates 为空对象、search_query 为空、clarification 为 null。
用户明确保持不变的历史字段不得重复写入 requirement_updates。仅要求记住偏好且不要行程时 intent 选 qa，不进入 plan/modify；明确换酒店但区域不变时不得更新 hotel_area。"""

CURRENT_DATE = "2026-09-17"

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


def tool(
    name: str,
    city: str = "",
    query: str = "",
    origin: str = "",
    destination: str = "",
    date: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "city": city,
        "query": query,
        "origin": origin,
        "destination": destination,
        "date": date,
    }


def decision(
    intent: str,
    domains: tuple[str, ...] = (),
    updates: dict[str, Any] | None = None,
    search_query: str = "",
    clarification: str | None = None,
    tools: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    return {
        "intent": intent,
        "domains": list(domains),
        "search_query": search_query,
        "requirement_updates": updates or {},
        "clarification": clarification,
        "tools": list(tools),
    }


def validate_answer(answer: dict[str, Any]) -> None:
    assert set(answer) == TOP_KEYS, "顶层字段必须刚好等于协议字段"
    assert answer["intent"] in ALLOWED_INTENTS, f"非法 intent: {answer['intent']}"
    assert isinstance(answer["domains"], list) and not (set(answer["domains"]) - ALLOWED_DOMAINS)
    assert isinstance(answer["requirement_updates"], dict)
    updates = answer["requirement_updates"]
    assert not (set(updates) - ALLOWED_UPDATES), "存在非法需求字段"
    if "pace" in updates:
        assert updates["pace"] in {"relaxed", "compact"}
    for key in ("interests", "locked_activities"):
        if key in updates:
            value = updates[key]
            assert isinstance(value, list) and value and all(isinstance(item, str) and item.strip() for item in value)
    if "diet" in updates:
        assert isinstance(updates["diet"], str) and updates["diet"].strip()
    for key in ("days", "travelers", "rooms"):
        if key in updates:
            assert isinstance(updates[key], int) and not isinstance(updates[key], bool) and updates[key] >= 1
    if "budget" in updates:
        assert isinstance(updates["budget"], (int, float)) and not isinstance(updates["budget"], bool) and updates["budget"] >= 0
    for key in ("start_date", "end_date"):
        if key in updates:
            assert isinstance(updates[key], str)
    for item in answer["tools"]:
        assert set(item) == TOOL_KEYS, "工具字段必须固定六项"
        assert item["name"] in ALLOWED_TOOLS, f"非法工具: {item['name']}"
    if answer["intent"] == "out_of_scope":
        assert answer["domains"] == []
        assert answer["requirement_updates"] == {}
        assert answer["search_query"] == ""
        assert answer["clarification"] is None
        assert answer["tools"] == []


def sample(saved: dict[str, Any], utterance: str, answer: dict[str, Any]) -> dict[str, Any]:
    validate_answer(answer)
    user_content = (
        "当前已保存需求："
        + json.dumps(saved, ensure_ascii=False, separators=(",", ":"))
        + "。本轮用户："
        + utterance
    )
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False, separators=(",", ":"))},
        ]
    }


CONTEXTS = [
    {"destination": "深圳", "days": 3, "travelers": 2, "budget": 3000, "hotel_area": "福田中心区", "new_area": "宝安中心", "other_area": "南山", "attraction": "世界之窗", "station": "深圳北站"},
    {"destination": "成都", "days": 4, "travelers": 3, "budget": 6000, "hotel_area": "春熙路", "new_area": "太古里", "other_area": "宽窄巷子", "attraction": "熊猫基地", "station": "双流机场"},
    {"destination": "杭州", "days": 3, "travelers": 3, "budget": 4000, "hotel_area": "西湖东侧", "new_area": "武林广场", "other_area": "湖滨", "attraction": "西湖", "station": "杭州东站"},
    {"destination": "上海", "days": 4, "travelers": 2, "budget": 7000, "hotel_area": "静安寺", "new_area": "陆家嘴", "other_area": "人民广场", "attraction": "外滩", "station": "虹桥机场"},
    {"destination": "北京", "days": 5, "travelers": 4, "budget": 9000, "hotel_area": "前门", "new_area": "国贸", "other_area": "王府井", "attraction": "故宫", "station": "北京南站"},
    {"destination": "西安", "days": 3, "travelers": 2, "budget": 4500, "hotel_area": "钟楼", "new_area": "大雁塔", "other_area": "小寨", "attraction": "兵马俑", "station": "咸阳机场"},
]


def build_protocol_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for ctx in CONTEXTS:
        city = ctx["destination"]
        old = ctx["hotel_area"]
        new = ctx["new_area"]
        other = ctx["other_area"]
        attraction = ctx["attraction"]
        station = ctx["station"]
        days = ctx["days"]

        # 酒店区域保持不变：不写 requirement_updates.hotel_area
        for user_text in (
            f"住宿继续放在{old}，位置不用改。",
            f"酒店区域沿用{old}就行。",
            "住处保持原来的酒店位置，不调整住宿片区。",
            f"还是住{old}，住宿区域不要变动。",
        ):
            rows.append(sample(ctx, user_text, decision("qa", ("hotel",))))

        # 明确换住宿区域
        for user_text in (
            f"把酒店区域改到{new}，帮我找那边的住宿。",
            f"住{new}更方便，原来的住宿位置换掉。",
            f"酒店挪到{new}附近，预算暂时不变。",
            f"这次想住{new}一带，请按新区域找酒店。",
        ):
            rows.append(
                sample(
                    ctx,
                    user_text,
                    decision(
                        "modify",
                        ("hotel",),
                        {"hotel_area": new},
                        search_query=f"{city} {new} 酒店",
                        tools=(tool("hotel_search", city, new),),
                    ),
                )
            )

        # 酒店换一家但区域不变：只检索，不更新 hotel_area
        for user_text in (
            f"酒店换一家评价更好的，但区域仍然在{old}。",
            f"住的片区不变，帮我在{old}重新找一家酒店。",
            f"不调整住宿区域，只替换{old}当前那家酒店。",
            f"还是住{old}，酒店本身换成安静一点的。",
        ):
            rows.append(
                sample(
                    ctx,
                    user_text,
                    decision(
                        "modify",
                        ("hotel",),
                        search_query=f"{old} 酒店",
                        tools=(tool("hotel_search", city, old),),
                    ),
                )
            )

        # 饮食限制
        for user_text, diet in (
            ("同行有人不能吃辣，餐厅按不吃辣来找。", "不吃辣"),
            ("饮食尽量清淡，别安排重口味。", "清淡"),
            ("有素食者，吃饭只考虑素食友好的地方。", "素食"),
            ("孩子对花生过敏，选餐厅时避开花生。", "花生过敏"),
        ):
            rows.append(
                sample(
                    ctx,
                    user_text,
                    decision(
                        "modify",
                        ("food",),
                        {"diet": diet},
                        search_query=f"{city} {diet} 餐厅",
                        tools=(tool("restaurant_search", city, diet),),
                    ),
                )
            )

        # 人数与房数
        for user_text, travelers, rooms in (
            ("同行人数改为四位，住宿需要两间房。", 4, 2),
            ("一共六个人，住宿要订三间房。", 6, 3),
            ("改成三个人出行，酒店要两间房。", 3, 2),
            ("我们五个人住，安排三间房更合适。", 5, 3),
        ):
            rows.append(
                sample(
                    ctx,
                    user_text,
                    decision(
                        "modify",
                        ("travelers", "hotel"),
                        {"travelers": travelers, "rooms": rooms},
                        search_query=f"{city} {rooms}间房",
                        tools=(tool("hotel_search", city, f"{rooms}间房"),),
                    ),
                )
            )

        # 日期范围：显式日期为准，同时推导 days
        for user_text, start, end, trip_days in (
            ("2026年10月1日出发，10月4日返回。", "2026-10-01", "2026-10-04", 4),
            ("改成2026年12月15日至12月18日出行。", "2026-12-15", "2026-12-18", 4),
            ("2027年1月12号到16号。", "2027-01-12", "2027-01-16", 5),
            ("2027年2月3日开始，2月5日返程。", "2027-02-03", "2027-02-05", 3),
            ("2027年3月18日出发，2027年3月21日回来。", "2027-03-18", "2027-03-21", 4),
        ):
            rows.append(
                sample(
                    ctx,
                    user_text,
                    decision(
                        "modify",
                        ("itinerary", "dates"),
                        {"start_date": start, "end_date": end, "days": trip_days},
                    ),
                )
            )

        # 兴趣偏好与必去活动
        rows.append(
            sample(
                ctx,
                f"孩子最期待{attraction}和博物馆。",
                decision(
                    "modify",
                    ("attraction",),
                    {"interests": [attraction, "博物馆"]},
                    search_query=f"{city} {attraction} 博物馆",
                    tools=(tool("poi_search", city, attraction),),
                ),
            )
        )
        rows.append(
            sample(
                ctx,
                f"{attraction}一定要保留。",
                decision("modify", ("attraction",), {"locked_activities": [attraction]}),
            )
        )

        # 行程节奏
        rows.append(
            sample(
                ctx,
                "带老人出行，每天别排太紧。",
                decision("modify", ("itinerary",), {"pace": "relaxed"}),
            )
        )
        rows.append(
            sample(
                ctx,
                "时间比较赶，尽量多看几个地方。",
                decision("modify", ("itinerary",), {"pace": "compact"}),
            )
        )

        # 预算/人数不变：不要写入 requirement_updates
        rows.append(
            sample(
                ctx,
                f"花费按原来的{ctx['budget']}控制，不需要改预算。",
                decision("qa", ("budget",)),
            )
        )
        rows.append(
            sample(
                ctx,
                "同行人数和之前一样。",
                decision("qa", ("travelers",)),
            )
        )

        # 仅记忆偏好，不规划
        rows.append(
            sample(
                ctx,
                "请记住我喜欢海边和夜景，暂时不用做路线。",
                decision("qa", ("attraction",), {"interests": ["海边", "夜景"]}),
            )
        )
        rows.append(
            sample(
                ctx,
                "先记一下我不吃辣，现在不用安排餐厅。",
                decision("qa", ("food",), {"diet": "不吃辣"}),
            )
        )

        # 天气、路线
        rows.append(
            sample(
                ctx,
                f"明天{city}需要带伞吗？",
                decision("qa", ("weather",), search_query=f"{city} 明天天气", tools=(tool("weather_query", city),)),
            )
        )
        rows.append(
            sample(
                ctx,
                f"从{station}到{old}怎么坐地铁？",
                decision(
                    "qa",
                    ("transportation",),
                    search_query=f"{station}到{old}公共交通",
                    tools=(tool("route_query", city, origin=station, destination=old),),
                ),
            )
        )

        # 探索推荐
        rows.append(
            sample(
                ctx,
                f"附近有什么适合第一次去的景点？",
                decision("explore", ("attraction",), search_query=f"{city} 热门景点", tools=(tool("poi_search", city, "热门景点"),)),
            )
        )
        rows.append(
            sample(
                ctx,
                f"{old}附近有什么清淡一点的本地菜？",
                decision("explore", ("food",), search_query=f"{city} {old} 本地菜", tools=(tool("restaurant_search", city, "清淡 本地菜"),)),
            )
        )
        rows.append(
            sample(
                ctx,
                f"{city}有性价比高的酒店吗？",
                decision("explore", ("hotel",), search_query=f"{city} 性价比高 酒店", tools=(tool("hotel_search", city, "性价比高"),)),
            )
        )

        # 比较
        rows.append(
            sample(
                ctx,
                f"{old}和{other}相比，住哪里出行更方便？",
                decision("compare", ("hotel",), search_query=f"{old} {other} 住宿比较", tools=(tool("hotel_search", city, f"{old} {other}"),)),
            )
        )

        # 预订引导
        rows.append(
            sample(
                ctx,
                f"我想订{new}附近的房间。",
                decision(
                    "booking",
                    ("hotel",),
                    {"hotel_area": new},
                    clarification="酒店预订需前往官方平台完成",
                    tools=(tool("hotel_inventory", city, new),),
                ),
            )
        )
        rows.append(
            sample(
                ctx,
                f"帮我看看{attraction}门票还能订吗？",
                decision(
                    "booking",
                    ("attraction",),
                    search_query=f"{attraction} 门票",
                    clarification="门票预订需前往官方平台完成",
                    tools=(tool("ticket_search", city, attraction),),
                ),
            )
        )

        # 完整规划 / 格式整理
        rows.append(
            sample(
                ctx,
                f"按当前条件规划{city}{days}天行程。",
                decision("plan", ("itinerary",)),
            )
        )
        rows.append(
            sample(
                ctx,
                "把已有行程整理成按天的表格。",
                decision("format", ("itinerary",)),
            )
        )

        # 目的地切换
        rows.append(
            sample(
                ctx,
                f"{city}先不去了，改成杭州玩四天。",
                decision("modify", ("itinerary", "dates"), {"destination": "杭州", "days": 4}),
            )
        )

        # 歧义追问
        rows.append(
            sample(
                ctx,
                "把前面第二个景点换掉。",
                decision("modify", ("attraction",), clarification="请确认要替换的是哪个景点，以及希望换成什么类型？"),
            )
        )

    # 无关问题：必须清洗
    for ctx in CONTEXTS:
        for user_text in (
            "帮我解释一下 Python 装饰器是什么？",
            "帮我写一个爬虫。",
            "比特币现在能买吗？",
            "做一道高数题。",
            "不用管旅行，讲讲 LangChain 原理。",
            "把刚才的行程代码写成 Python。",
        ):
            rows.append(sample(ctx, user_text, decision("out_of_scope")))

    # 混合问题：只处理旅游部分
    rows.append(sample(CONTEXTS[0], "别讲 Python，直接告诉我深圳三天怎么安排。", decision("plan", ("itinerary",))))
    rows.append(
        sample(
            CONTEXTS[0],
            "顺便解释 Python 装饰器，再推荐福田附近酒店。",
            decision("explore", ("hotel",), search_query="福田附近酒店", tools=(tool("hotel_search", "深圳", "福田"),)),
        )
    )

    # 问候
    rows.append(sample(CONTEXTS[0], "你好呀。", decision("social")))

    return rows


def expand_with_light_variants(rows: list[dict[str, Any]], max_samples: int) -> list[dict[str, Any]]:
    heads = ["", "请注意，", "我想确认一下："]
    tails = ["", "，别漏掉我说的条件。"]
    expanded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        content = row["messages"][1]["content"]
        prefix, utterance = content.split("本轮用户：", 1)
        for head in heads:
            for tail in tails:
                user_msg = prefix + "本轮用户：" + head + utterance + tail
                clone = json.loads(json.dumps(row, ensure_ascii=False))
                clone["messages"][1]["content"] = user_msg
                key = json.dumps(clone, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    expanded.append(clone)
                    seen.add(key)
    random.Random(20260917).shuffle(expanded)
    if len(expanded) < max_samples:
        repeats = (max_samples + len(expanded) - 1) // len(expanded)
        expanded = (expanded * repeats)[:max_samples]
    return expanded[:max_samples]


def register_dataset(info_path: Path, dataset_name: str, relative_file: str) -> None:
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    info[dataset_name] = {
        "file_name": relative_file,
        "formatting": "sharegpt",
        "columns": {"messages": "messages"},
        "tags": {
            "role_tag": "role",
            "content_tag": "content",
            "system_tag": "system",
            "user_tag": "user",
            "assistant_tag": "assistant",
        },
    }
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/root/autodl-tmp/trippilot-finetune/data/formal/train/trippilot_preference_protocol_v1.json")
    parser.add_argument("--dataset-info", default="/root/autodl-tmp/trippilot-finetune/data/dataset_info.json")
    parser.add_argument("--dataset-name", default="trippilot_preference_protocol_v1")
    parser.add_argument("--max-samples", type=int, default=720)
    args = parser.parse_args()

    rows = expand_with_light_variants(build_protocol_rows(), args.max_samples)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    register_dataset(Path(args.dataset_info), args.dataset_name, "formal/train/" + output.name)

    intents = Counter(json.loads(item["messages"][2]["content"])["intent"] for item in rows)
    print(f"Protocol-V1 精筛样本数：{len(rows)}")
    print("intent 分布：" + json.dumps(dict(sorted(intents.items())), ensure_ascii=False))
    print(f"输出文件：{output}")
    print(f"数据集注册名：{args.dataset_name}")


if __name__ == "__main__":
    main()
