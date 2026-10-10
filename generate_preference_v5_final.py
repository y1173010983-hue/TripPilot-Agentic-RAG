"""Generate the final TripPilot V5 correction dataset from V4 failure families."""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path


SYSTEM = """你是 TripPilot 旅游需求理解器。只处理旅游需求、旅游偏好和相关追问，只输出一个合法 JSON 对象，不输出解释或 Markdown。顶层字段必须且只能是 intent、domains、search_query、requirement_updates、clarification、tools。intent 只能是 out_of_scope、social、qa、explore、compare、plan、modify、format、booking。requirement_updates 只填写本轮明确改变或明确要求记住的字段，允许 destination、departure、start_date、end_date、days、travelers、budget、budget_scope、pace、interests、diet、hotel_area、rooms、locked_activities。历史中已有且本轮明确保持不变的字段不得重复写入。pace 只能是 relaxed 或 compact；interests 和 locked_activities 必须是字符串数组；diet 必须是字符串。tools 中每项必须包含 name、city、query、origin、destination、date，工具名只能是 weather_query、poi_search、restaurant_search、hotel_search、route_query、ticket_search、hotel_inventory、intercity_search。比较住宿区域使用 compare；明确想订房使用 booking 和 hotel_inventory；只更换酒店但区域不变时不得更新 hotel_area；只说记住偏好且暂不规划时使用 qa、保存明确偏好且不调用规划工具；无关问题使用 out_of_scope，且 domains、tools 为空数组，search_query 为空，requirement_updates 为空对象。"""

TOP_KEYS = {"intent", "domains", "search_query", "requirement_updates", "clarification", "tools"}
ALLOWED_INTENTS = {"out_of_scope", "social", "qa", "explore", "compare", "plan", "modify", "format", "booking"}
ALLOWED_UPDATES = {"destination", "departure", "start_date", "end_date", "days", "travelers", "budget", "budget_scope", "pace", "interests", "diet", "hotel_area", "rooms", "locked_activities"}
ALLOWED_TOOLS = {"weather_query", "poi_search", "restaurant_search", "hotel_search", "route_query", "ticket_search", "hotel_inventory", "intercity_search"}


def tool(name: str, city: str = "", query: str = "", origin: str = "", destination: str = "", date: str = "") -> dict:
    return {"name": name, "city": city, "query": query, "origin": origin, "destination": destination, "date": date}


def decision(intent: str, domains=(), updates=None, search_query: str = "", clarification=None, tools=()) -> dict:
    return {
        "intent": intent,
        "domains": list(domains),
        "search_query": search_query,
        "requirement_updates": updates or {},
        "clarification": clarification,
        "tools": list(tools),
    }


def sample(saved: dict, utterance: str, answer: dict) -> dict:
    validate_answer(answer)
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": "当前已保存需求：" + json.dumps(saved, ensure_ascii=False, separators=(",", ":")) + "。本轮用户：" + utterance,
            },
            {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False, separators=(",", ":"))},
        ]
    }


def validate_answer(answer: dict) -> None:
    assert set(answer) == TOP_KEYS
    assert answer["intent"] in ALLOWED_INTENTS
    assert not (set(answer["requirement_updates"]) - ALLOWED_UPDATES)
    if "pace" in answer["requirement_updates"]:
        assert answer["requirement_updates"]["pace"] in {"relaxed", "compact"}
    for key in ("interests", "locked_activities"):
        if key in answer["requirement_updates"]:
            assert isinstance(answer["requirement_updates"][key], list)
    if "diet" in answer["requirement_updates"]:
        assert isinstance(answer["requirement_updates"]["diet"], str)
    for item in answer["tools"]:
        assert set(item) == {"name", "city", "query", "origin", "destination", "date"}
        assert item["name"] in ALLOWED_TOOLS


CONTEXTS = [
    {"destination": "深圳", "days": 3, "travelers": 2, "budget": 3000, "hotel_area": "福田中心区"},
    {"destination": "成都", "days": 4, "travelers": 3, "budget": 6000, "hotel_area": "春熙路"},
    {"destination": "杭州", "days": 3, "travelers": 3, "budget": 4000, "hotel_area": "西湖东侧"},
    {"destination": "上海", "days": 4, "travelers": 2, "budget": 7000, "hotel_area": "静安寺"},
    {"destination": "北京", "days": 5, "travelers": 4, "budget": 9000, "hotel_area": "前门"},
    {"destination": "西安", "days": 3, "travelers": 2, "budget": 4500, "hotel_area": "钟楼"},
]

NEW_AREAS = {"深圳": "后海", "成都": "太古里", "杭州": "武林广场", "上海": "人民广场", "北京": "国贸", "西安": "大雁塔"}
COMPARE_AREAS = {"深圳": "南山", "成都": "宽窄巷子", "杭州": "湖滨", "上海": "陆家嘴", "北京": "王府井", "西安": "小寨"}


def build_targeted_rows() -> list[dict]:
    rows: list[dict] = []
    no_change_patterns = [
        "住宿继续放在{old}，位置不用改。",
        "酒店区域沿用{old}就行。",
        "还是住{old}，别调整住宿片区。",
        "住处保持原来的{old}，其他安排可以再看。",
    ]
    change_patterns = [
        "把酒店区域改到{new}，帮我找那边的住宿。",
        "住{new}更方便，原来的住宿位置换掉。",
        "酒店挪到{new}附近，预算暂时不变。",
        "这次想住{new}一带，请按新区域找酒店。",
    ]
    diet_patterns = [
        ("同行有人不能吃辣，餐厅按不吃辣来找。", "不吃辣"),
        ("饮食尽量清淡，别安排重口味。", "清淡"),
        ("有素食者，吃饭只考虑素食友好的地方。", "素食"),
        ("孩子对花生过敏，选餐厅时避开花生。", "花生过敏"),
    ]
    room_patterns = [
        ("同行人数改为四位，住宿需要两间房。", 4, 2),
        ("一共六个人，住宿要订三间房。", 6, 3),
        ("改成三个人出行，酒店要两间房。", 3, 2),
        ("我们五个人住，安排三间房更合适。", 5, 3),
    ]
    food_patterns = [
        ("{area}附近有哪些清淡一点的本地菜？", "清淡 本地菜"),
        ("想找{area}周边适合带孩子吃的餐厅。", "亲子餐厅"),
        ("{area}一带哪里能吃到当地特色早餐？", "当地特色早餐"),
        ("帮我查{area}附近评价好的素食餐厅。", "素食餐厅"),
    ]
    compare_patterns = [
        "{old}和{other}相比，住哪里出行更方便？",
        "住宿在{old}还是{other}更合适，帮我比较一下。",
        "对比一下{old}与{other}的交通和游玩便利度。",
        "如果主要看景点，{old}和{other}哪个住宿区域更好？",
    ]
    booking_patterns = [
        "我想订{area}附近的房间。",
        "帮我看看{area}一带有没有可以预订的酒店。",
        "准备下单住{area}，查一下房态。",
        "要订{area}周边两晚住宿，请查可订房间。",
    ]
    memory_patterns = [
        ("请记住我喜欢海边和夜景，暂时不用规划路线。", {"interests": ["海边", "夜景"]}),
        ("先记一下我偏爱博物馆和历史建筑，不用现在排行程。", {"interests": ["博物馆", "历史建筑"]}),
        ("记住我吃饭要清淡，先别推荐餐厅。", {"diet": "清淡"}),
        ("把熊猫基地设成一定要去的活动，暂时不要重新规划。", {"locked_activities": ["熊猫基地"]}),
    ]
    mixed_plan_patterns = [
        "别解释 Python，直接给我安排{city}{days}天旅游行程。",
        "技术问题不用回答，按现有需求规划{city}{days}日游。",
        "先忽略我前面问的编程问题，帮我做{city}{days}天路线。",
        "不聊股票了，请根据保存的需求生成{city}{days}天行程。",
    ]
    same_area_replace_patterns = [
        "酒店换一家评价更好的，但区域仍然在{old}。",
        "住的片区不变，帮我在{old}重新找一家酒店。",
        "不调整住宿区域，只替换{old}当前那家酒店。",
        "还是住{old}，酒店本身换成安静一点的。",
    ]
    multi_patterns = [
        ("这次共四人出行，必须安排{interest}，餐食清淡，整体节奏放松。", 4, "清淡", "relaxed"),
        ("改成五人同行，重点玩{interest}，不能吃辣，每天轻松些。", 5, "不吃辣", "relaxed"),
        ("三个人出发，必须去{interest}，想多看几个地方，餐食要素食。", 3, "素食", "compact"),
        ("两人旅行，保留{interest}，少走路，吃饭避开花生。", 2, "花生过敏", "relaxed"),
    ]
    interests = {"深圳": "世界之窗", "成都": "熊猫基地", "杭州": "西湖", "上海": "外滩", "北京": "故宫", "西安": "兵马俑"}

    for ctx in CONTEXTS:
        city, old = ctx["destination"], ctx["hotel_area"]
        new, other = NEW_AREAS[city], COMPARE_AREAS[city]
        for pattern in no_change_patterns:
            rows.append(sample(ctx, pattern.format(old=old), decision("qa", ["hotel"])))
        for pattern in change_patterns:
            utterance = pattern.format(new=new)
            rows.append(sample(ctx, utterance, decision("modify", ["hotel"], {"hotel_area": new}, f"{city} {new} 酒店", tools=[tool("hotel_search", city, new)])))
        for utterance, diet in diet_patterns:
            rows.append(sample(ctx, utterance, decision("modify", ["food"], {"diet": diet}, f"{city} {diet} 餐厅", tools=[tool("restaurant_search", city, diet)])))
        for utterance, travelers, rooms in room_patterns:
            rows.append(sample(ctx, utterance, decision("modify", ["hotel", "travelers"], {"travelers": travelers, "rooms": rooms}, f"{city} {rooms}间房", tools=[tool("hotel_search", city, f"{rooms}间房")])))
        for pattern, query in food_patterns:
            utterance = pattern.format(area=old)
            full_query = f"{old} {query}"
            rows.append(sample(ctx, utterance, decision("qa", ["food"], search_query=full_query, tools=[tool("restaurant_search", city, full_query)])))
        for pattern in compare_patterns:
            utterance = pattern.format(old=old, other=other)
            query = f"{old} {other} 住宿比较"
            rows.append(sample(ctx, utterance, decision("compare", ["hotel"], search_query=query, tools=[tool("hotel_search", city, query)])))
        for pattern in booking_patterns:
            utterance = pattern.format(area=new)
            rows.append(sample(ctx, utterance, decision("booking", ["hotel"], search_query=f"{new} 酒店房态", tools=[tool("hotel_inventory", city, new)])))
        for utterance, updates in memory_patterns:
            rows.append(sample(ctx, utterance, decision("qa", ["attraction"] if "interests" in updates or "locked_activities" in updates else ["food"], updates)))
        for pattern in mixed_plan_patterns:
            utterance = pattern.format(city=city, days=ctx["days"])
            rows.append(sample(ctx, utterance, decision("plan", ["itinerary"])))
        for pattern in same_area_replace_patterns:
            utterance = pattern.format(old=old)
            rows.append(sample(ctx, utterance, decision("modify", ["hotel"], search_query=f"{old} 酒店", tools=[tool("hotel_search", city, old)])))
        interest = interests[city]
        for pattern, travelers, diet, pace in multi_patterns:
            utterance = pattern.format(interest=interest)
            updates = {"travelers": travelers, "interests": [interest], "diet": diet, "pace": pace}
            rows.append(sample(ctx, utterance, decision("modify", ["travelers", "attraction", "food", "itinerary"], updates, tools=[tool("poi_search", city, interest), tool("restaurant_search", city, diet)])))

    date_rows = [
        (CONTEXTS[0], "2026年10月1日出发，10月4日返回。", "2026-10-01", "2026-10-04", 4),
        (CONTEXTS[1], "行程改成2026年10月8日至10月12日。", "2026-10-08", "2026-10-12", 5),
        (CONTEXTS[2], "十一月三号到杭州，六号返程。", "2026-11-03", "2026-11-06", 4),
        (CONTEXTS[3], "出发日期定在2026年11月10日，11月13日结束。", "2026-11-10", "2026-11-13", 4),
        (CONTEXTS[4], "12月2日出发，12月7日回来。", "2026-12-02", "2026-12-07", 6),
        (CONTEXTS[5], "改为2026年12月15日至12月18日出行。", "2026-12-15", "2026-12-18", 4),
        (CONTEXTS[0], "2027年1月5日到，1月8日走。", "2027-01-05", "2027-01-08", 4),
        (CONTEXTS[1], "旅程日期换到2027年1月12号到16号。", "2027-01-12", "2027-01-16", 5),
        (CONTEXTS[2], "2027年2月3日开始，2月5日返程。", "2027-02-03", "2027-02-05", 3),
        (CONTEXTS[3], "把出行时间改为2027-02-10到2027-02-14。", "2027-02-10", "2027-02-14", 5),
        (CONTEXTS[4], "定在2027年3月1日出发，3月6日结束。", "2027-03-01", "2027-03-06", 6),
        (CONTEXTS[5], "2027-03-18出发，2027-03-21回来。", "2027-03-18", "2027-03-21", 4),
    ]
    for ctx, utterance, start, end, days in date_rows:
        rows.append(sample(ctx, utterance, decision("modify", ["itinerary"], {"start_date": start, "end_date": end, "days": days})))
    return rows


def build_replay_rows() -> list[dict]:
    rows: list[dict] = []
    for ctx in CONTEXTS:
        city = ctx["destination"]
        rows.extend([
            sample(ctx, "明天需要带伞吗？", decision("qa", ["weather"], search_query=f"{city} 明天天气", tools=[tool("weather_query", city)])),
            sample(ctx, "附近有什么适合第一次去的景点？", decision("explore", ["attraction"], search_query=f"{city} 热门景点", tools=[tool("poi_search", city, "热门景点")])),
            sample(ctx, "把已有行程整理成按天表格。", decision("format", ["itinerary"])),
            sample(ctx, "你好，能帮我规划旅行吗？", decision("social")),
            sample(ctx, "解释一下 FastAPI 依赖注入。", decision("out_of_scope")),
            sample(ctx, "帮我预测明天的股票走势。", decision("out_of_scope")),
            sample(ctx, "预算维持原来的数额，不需要调整。", decision("qa", ["budget"])),
            sample(ctx, "同行人数和之前一样。", decision("qa", ["travelers"])),
            sample(ctx, "行程放慢一点，每天留出休息时间。", decision("modify", ["itinerary"], {"pace": "relaxed"})),
            sample(ctx, "时间很紧，尽量多安排几个景点。", decision("modify", ["itinerary"], {"pace": "compact"})),
        ])
    route_cases = [
        (CONTEXTS[0], "从深圳北站到福田中心区怎么坐地铁？", "深圳北站", "福田中心区"),
        (CONTEXTS[1], "从双流机场到春熙路公共交通怎么走？", "双流机场", "春熙路"),
        (CONTEXTS[2], "杭州东站去西湖东侧怎么走？", "杭州东站", "西湖东侧"),
        (CONTEXTS[3], "虹桥机场到静安寺怎么坐车？", "虹桥机场", "静安寺"),
        (CONTEXTS[4], "北京南站到前门的地铁路线是什么？", "北京南站", "前门"),
        (CONTEXTS[5], "咸阳机场去钟楼怎么走方便？", "咸阳机场", "钟楼"),
    ]
    for ctx, utterance, origin, destination in route_cases:
        city = ctx["destination"]
        rows.append(sample(ctx, utterance, decision("qa", ["transportation"], search_query=f"{origin} {destination}", tools=[tool("route_query", city, origin=origin, destination=destination)])))
    return rows


def deduplicate(rows: list[dict]) -> list[dict]:
    result, seen = [], set()
    for item in rows:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def register_dataset(info_path: Path, dataset_name: str, relative_file: str) -> None:
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    info[dataset_name] = {
        "file_name": relative_file,
        "formatting": "sharegpt",
        "columns": {"messages": "messages"},
        "tags": {"role_tag": "role", "content_tag": "content", "system_tag": "system", "user_tag": "user", "assistant_tag": "assistant"},
    }
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/root/autodl-tmp/trippilot-finetune/data/formal/train/trippilot_preference_final_v5.json")
    parser.add_argument("--dataset-info", default="/root/autodl-tmp/trippilot-finetune/data/dataset_info.json")
    parser.add_argument("--dataset-name", default="trippilot_preference_final_v5")
    args = parser.parse_args()

    rows = deduplicate(build_targeted_rows() + build_replay_rows())
    random.Random(20260917).shuffle(rows)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    register_dataset(Path(args.dataset_info), args.dataset_name, "formal/train/" + output.name)

    intents = Counter(json.loads(item["messages"][2]["content"])["intent"] for item in rows)
    print(f"V5 高质量样本数：{len(rows)}")
    print("intent 分布：" + json.dumps(dict(sorted(intents.items())), ensure_ascii=False))
    print(f"数据文件：{output}")
    print(f"数据集注册名：{args.dataset_name}")


if __name__ == "__main__":
    main()
