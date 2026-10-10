"""Generate curated TripPilot semantic-router train/dev/test datasets."""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path
from typing import Callable


SYSTEM = """你是 TripPilot 旅游语义解析器。你只负责把本轮用户表达转换成标准业务语义，不负责选择工具，也不直接回答用户。只输出一个合法 JSON 对象，字段必须且只能是 operation、domains、entities、state_changes、clarification。
operation 只能是 ask、explore、compare、plan、book、remember、update、format、social、out_of_scope。
domains 是数组，元素只能是 hotel、food、attraction、transportation、weather、itinerary、general。
entities 保存本轮执行需要使用的实体；state_changes 只保存需要写入长期旅行状态的变化。每个 state_changes 项必须包含 field、operation、value，其中 operation 只能是 set、keep、remove。用户明确保持原值时使用 keep；明确取消条件时使用 remove；新增或修改时使用 set。同一句中出现反复修改时只保留最后确认的值。
clarification 在信息不足且无法安全执行时填写自然语言澄清问题，否则为 null。非旅游请求必须使用 out_of_scope，domains 为空数组，entities 为空对象，state_changes 为空数组。"""

OPERATIONS = {"ask", "explore", "compare", "plan", "book", "remember", "update", "format", "social", "out_of_scope"}
DOMAINS = {"hotel", "food", "attraction", "transportation", "weather", "itinerary", "general"}
FIELDS = {"destination", "departure", "start_date", "end_date", "days", "travelers", "budget", "budget_scope", "pace", "interests", "diet", "hotel_area", "rooms", "locked_activities"}
TOP_KEYS = {"operation", "domains", "entities", "state_changes", "clarification"}
CURRENT_DATE = "2026-09-16"


def change(field: str, operation: str, value=None) -> dict:
    return {"field": field, "operation": operation, "value": value}


def semantic(operation: str, domains=(), entities=None, changes=(), clarification=None) -> dict:
    return {
        "operation": operation,
        "domains": list(domains),
        "entities": entities or {},
        "state_changes": list(changes),
        "clarification": clarification,
    }


def validate_answer(answer: dict) -> None:
    assert set(answer) == TOP_KEYS
    assert answer["operation"] in OPERATIONS
    assert isinstance(answer["domains"], list) and not (set(answer["domains"]) - DOMAINS)
    assert isinstance(answer["entities"], dict)
    assert isinstance(answer["state_changes"], list)
    for item in answer["state_changes"]:
        assert set(item) == {"field", "operation", "value"}
        assert item["field"] in FIELDS
        assert item["operation"] in {"set", "keep", "remove"}
        if item["operation"] == "remove":
            assert item["value"] is None
    if answer["operation"] == "out_of_scope":
        assert answer["domains"] == [] and answer["entities"] == {} and answer["state_changes"] == []


def row(state: dict, user_input: str, answer: dict) -> dict:
    validate_answer(answer)
    payload = {"current_date": CURRENT_DATE, "current_state": state, "user_input": user_input}
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
            {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False, separators=(",", ":"))},
        ]
    }


CONTEXTS = [
    {"destination": "深圳", "days": 3, "travelers": 2, "budget": 3000, "hotel_area": "福田中心区"},
    {"destination": "成都", "days": 4, "travelers": 3, "budget": 6000, "hotel_area": "春熙路"},
    {"destination": "杭州", "days": 3, "travelers": 3, "budget": 4000, "hotel_area": "西湖东侧"},
    {"destination": "上海", "days": 4, "travelers": 2, "budget": 7000, "hotel_area": "静安寺"},
    {"destination": "北京", "days": 5, "travelers": 4, "budget": 9000, "hotel_area": "前门"},
    {"destination": "西安", "days": 3, "travelers": 2, "budget": 4500, "hotel_area": "钟楼"},
]

NEW_AREA = {"深圳": "宝安中心", "成都": "太古里", "杭州": "武林广场", "上海": "陆家嘴", "北京": "国贸", "西安": "大雁塔"}
OTHER_AREA = {"深圳": "后海", "成都": "宽窄巷子", "杭州": "湖滨", "上海": "人民广场", "北京": "王府井", "西安": "小寨"}
ATTRACTION = {"深圳": "世界之窗", "成都": "熊猫基地", "杭州": "西湖", "上海": "外滩", "北京": "故宫", "西安": "兵马俑"}
STATION = {"深圳": "深圳北站", "成都": "双流机场", "杭州": "杭州东站", "上海": "虹桥机场", "北京": "北京南站", "西安": "咸阳机场"}


def add_group(
    splits: dict[str, list[dict]],
    state: dict,
    utterances: list[str],
    answer_builder: Callable[[str], dict],
) -> None:
    assert len(utterances) == 5
    for index, utterance in enumerate(utterances):
        split = "train" if index < 3 else "dev" if index == 3 else "test"
        splits[split].append(row(state, utterance, answer_builder(utterance)))


def build_splits() -> dict[str, list[dict]]:
    splits = {"train": [], "dev": [], "test": []}
    for state in CONTEXTS:
        city = state["destination"]
        old, new, other = state["hotel_area"], NEW_AREA[city], OTHER_AREA[city]
        attraction, station = ATTRACTION[city], STATION[city]

        add_group(splits, state, [
            f"把住宿区域改到{new}。", f"后面都住{new}一带。", f"原来的酒店位置换成{new}。",
            f"住在{new}对我更方便，调整一下。", f"住宿地点改为{new}，其他条件不变。",
        ], lambda _u, a=new: semantic("update", ["hotel"], {"hotel_area": a}, [change("hotel_area", "set", a)]))

        add_group(splits, state, [
            f"酒店区域继续保持{old}。", f"住处还是原来的{old}。", f"不要调整住宿片区，仍然住{old}。",
            f"住宿位置沿用{old}就好。", f"酒店地点不变，还是{old}。",
        ], lambda _u, a=old: semantic("update", ["hotel"], {"hotel_area": a}, [change("hotel_area", "keep", a)]))

        add_group(splits, state, [
            f"我想订{new}附近的房间。", f"查一下{new}还有没有能住的酒店。", f"准备把住宿定在{new}，看看房态。",
            f"帮我确认{new}周边是否还有空房。", f"想在{new}落实住宿，查询可以预订的房间。",
        ], lambda _u, a=new: semantic("book", ["hotel"], {"hotel_area": a}, []))

        add_group(splits, state, [
            f"比较一下{old}和{other}住哪里方便。", f"{old}与{other}哪个更适合住宿？", f"分析{old}、{other}两个住宿区域的差别。",
            f"如果看交通和景点，{old}还是{other}更好？", f"在{old}与{other}之间选住宿，请帮我对比。",
        ], lambda _u, a=old, b=other: semantic("compare", ["hotel"], {"compare_targets": [a, b]}, []))

        add_group(splits, state, [
            f"酒店换一家，区域还在{old}。", f"住的地方不变，但想重新选一家酒店。", f"还是住{old}，把当前酒店替换掉。",
            f"不换住宿片区，只在{old}另找酒店。", f"位置继续保留{old}，酒店本身换成安静点的。",
        ], lambda _u, a=old: semantic("explore", ["hotel"], {"hotel_area": a, "replacement": True}, [change("hotel_area", "keep", a)]))

        add_group(splits, state, [
            "同行有人不能吃辣，记到饮食要求里。", "把饮食偏好改成清淡。", "后面找餐厅要避开花生。",
            "请保存素食这个饮食条件。", "吃饭方面改为不吃海鲜。",
        ], lambda u: semantic("update", ["food"], {"diet": diet_from_text(u)}, [change("diet", "set", diet_from_text(u))]))

        room_values = [(4, 2), (6, 3), (3, 2), (5, 3), (2, 1)]
        room_utterances = [
            "改成四个人同行，需要两间房。", "一共六个人，住宿安排三间房。", "现在三个人出发，订两间房。",
            "我们五个人住，房间数量改成三间。", "最后确定两个人，只需要一间房。",
        ]
        for index, (utterance, values) in enumerate(zip(room_utterances, room_values)):
            travelers, rooms = values
            split = "train" if index < 3 else "dev" if index == 3 else "test"
            answer = semantic("update", ["hotel", "general"], {"travelers": travelers, "rooms": rooms}, [change("travelers", "set", travelers), change("rooms", "set", rooms)])
            splits[split].append(row(state, utterance, answer))

        add_group(splits, state, [
            f"记住我喜欢{attraction}和博物馆，先不用规划。", "先保存我喜欢夜景和历史建筑，不用排行程。", "请记下我偏爱自然风景，暂时别推荐。",
            "记住我喜欢当地市集和人文景点，先不做路线。", "把亲子乐园和动物园加入偏好，但现在不要规划。",
        ], lambda u: remember_answer(u, attraction))

        add_group(splits, state, [
            "记住我不能吃辣，暂时不用找餐厅。", "以后吃饭尽量清淡，先把偏好保存下来。", "请记下同行者对花生过敏，现在不需要推荐餐厅。",
            "保存素食偏好，暂时不做餐饮安排。", "把不吃海鲜记到长期饮食要求中。",
        ], lambda u: semantic("remember", ["food"], {"diet": diet_from_text(u)}, [change("diet", "set", diet_from_text(u))]))

        add_group(splits, state, [
            f"我想预订{attraction}的门票。", f"查一下{attraction}还有没有可订门票。", f"准备买{attraction}的票，看看是否能预订。",
            f"帮我确认{attraction}门票是否还有余量。", f"想把{attraction}门票定下来。",
        ], lambda _u, a=attraction, c=city: semantic("book", ["attraction"], {"city": c, "attraction_name": a}, []))

        add_group(splits, state, [
            f"比较{attraction}和当地博物馆哪个更值得去。", f"{attraction}与城市博物馆更推荐哪一个？", f"分析{attraction}和博物馆分别适合什么人。",
            f"如果时间只够一个，{attraction}和博物馆怎么选？", f"请对比{attraction}与当地博物馆的游玩特点。",
        ], lambda _u, a=attraction: semantic("compare", ["attraction"], {"compare_targets": [a, "当地博物馆"]}, []))

        add_group(splits, state, [
            f"按当前条件规划{city}{state['days']}天行程。", f"给我做一份{city}{state['days']}日游安排。", "根据已经保存的条件生成每天的路线。",
            "现在开始制定完整行程。", "把前面的旅游需求整理成可执行的旅行计划。",
        ], lambda _u, c=city, d=state["days"]: semantic("plan", ["itinerary"], {"destination": c, "days": d}, []))

        add_group(splits, state, [
            f"{city}还有哪些值得去的室内景点？", f"推荐几个适合第一次去{city}玩的地方。", f"想找{city}适合带长辈参观的景点。",
            f"探索一下{city}比较安静的小众景点。", f"看看{city}有哪些适合雨天游览的地方。",
        ], lambda u, c=city: semantic("explore", ["attraction"], {"city": c, "query": u}, []))

        add_group(splits, state, [
            f"{old}附近有什么清淡的本地菜？", f"帮我查{old}周边适合孩子吃的餐厅。", f"{old}一带哪里有当地特色早餐？",
            f"想知道{old}附近有哪些素食餐厅。", f"{old}周边有没有评价好的家常菜？",
        ], lambda u, c=city, a=old: semantic("ask", ["food"], {"city": c, "area": a, "query": u}, []))

        add_group(splits, state, [
            f"明天{city}会下雨吗？", f"查一下{city}明天的天气。", f"{city}明天适合户外活动吗？",
            f"我明天在{city}，需要带伞吗？", f"告诉我{city}明天的气温和天气。",
        ], lambda _u, c=city: semantic("ask", ["weather"], {"city": c, "date": "2026-09-17"}, []))

        add_group(splits, state, [
            f"从{station}到{old}怎么走？", f"查询{station}前往{old}的公共交通。", f"{station}去{old}坐什么方便？",
            f"帮我规划{station}到{old}的路线。", f"不打车的话，从{station}怎样到{old}？",
        ], lambda _u, c=city, o=station, d=old: semantic("ask", ["transportation"], {"city": c, "origin": o, "destination": d}, []))

        pace_values = ["relaxed", "compact", "relaxed", "compact", "relaxed"]
        pace_utterances = ["每天安排松一点，多留休息时间。", "时间比较紧，行程尽量紧凑。", "带老人出行，不要排得太赶。", "想多看几个地方，节奏可以快些。", "减少步行和赶路，整体轻松一点。"]
        for index, (utterance, pace) in enumerate(zip(pace_utterances, pace_values)):
            split = "train" if index < 3 else "dev" if index == 3 else "test"
            splits[split].append(row(state, utterance, semantic("update", ["itinerary"], {"pace": pace}, [change("pace", "set", pace)])))

        multi_values = [
            (4, "清淡", "relaxed"), (5, "不吃辣", "relaxed"), (3, "素食", "compact"), (2, "花生过敏", "relaxed"), (6, "不吃海鲜", "compact"),
        ]
        multi_utterances = [
            f"四个人去，必须安排{attraction}，餐食清淡，行程轻松。",
            f"改成五人同行，重点去{attraction}，不能吃辣，别太赶。",
            f"三个人出发，想看{attraction}，吃素，尽量多安排活动。",
            f"两个人旅行，保留{attraction}，少走路，还要避开花生。",
            f"六人同行，{attraction}一定要去，不吃海鲜，行程紧凑些。",
        ]
        for index, (utterance, values) in enumerate(zip(multi_utterances, multi_values)):
            travelers, diet, pace = values
            split = "train" if index < 3 else "dev" if index == 3 else "test"
            entities = {"travelers": travelers, "interests": [attraction], "diet": diet, "pace": pace}
            changes = [change("travelers", "set", travelers), change("interests", "set", [attraction]), change("diet", "set", diet), change("pace", "set", pace)]
            splits[split].append(row(state, utterance, semantic("update", ["general", "attraction", "food", "itinerary"], entities, changes)))

        add_group(splits, state, [
            "把当前行程整理成按天表格。", "用清单格式重新排版现有路线。", "把已有计划改成时间轴展示。",
            "按上午、下午、晚上整理现有行程。", "将目前方案整理为便于打印的每日表格。",
        ], lambda u: semantic("format", ["itinerary"], {"format_request": u}, []))

        add_group(splits, state, [
            "你好。", "谢谢你的帮助。", "你能做哪些旅游相关的事情？", "早上好，旅游助手。", "先这样，回头再规划。",
        ], lambda _u: semantic("social", ["general"], {}, []))

        add_group(splits, state, [
            "解释一下 Python 装饰器。", "帮我预测明天的股票走势。", "写一道数据库作业题。", "RAG 的向量检索是什么？", "推荐一只短期上涨的基金。",
        ], lambda _u: semantic("out_of_scope", [], {}, []))

        next_city = CONTEXTS[(CONTEXTS.index(state) + 1) % len(CONTEXTS)]["destination"]
        add_group(splits, state, [
            f"不去{city}了，目的地换成{next_city}。", f"把这次旅行改到{next_city}，天数先不变。", f"目的地从{city}调整为{next_city}。",
            f"之前的{city}计划取消，改去{next_city}。", f"最终决定去{next_city}，不再去{city}。",
        ], lambda _u, c=next_city: semantic("update", ["general"], {"destination": c}, [change("destination", "set", c)]))

        add_group(splits, state, [
            "预算维持原来的数额。", "同行人数没有变化。", f"酒店区域还是{old}。", "旅行天数继续按原计划。", f"目的地仍然是{city}。",
        ], lambda u, s=state: keep_answer(u, s))

        add_group(splits, state, [
            "把第二个景点删掉。", "换掉前面提到的那家酒店。", "把预算调高一点。", "后面的安排轻松一些。", "把刚才说的那个地方加进行程。",
        ], lambda u: ambiguous_answer(u))

    add_date_cases(splits)
    add_last_value_cases(splits)
    return finalize_splits(splits)


def diet_from_text(text: str) -> str:
    mappings = (
        ("不能吃辣", "不吃辣"),
        ("不吃辣", "不吃辣"),
        ("清淡", "清淡"),
        ("避开花生", "花生过敏"),
        ("花生过敏", "花生过敏"),
        ("素食", "素食"),
        ("不吃海鲜", "不吃海鲜"),
    )
    for marker, value in mappings:
        if marker in text:
            return value
    raise ValueError(text)


def remember_answer(text: str, attraction: str) -> dict:
    if attraction in text:
        values = [attraction, "博物馆"]
        return semantic("remember", ["attraction"], {"interests": values}, [change("interests", "set", values)])
    mappings = [
        ("夜景", ["夜景", "历史建筑"]),
        ("自然风景", ["自然风景"]),
        ("当地市集", ["当地市集", "人文景点"]),
        ("亲子乐园", ["亲子乐园", "动物园"]),
    ]
    for marker, values in mappings:
        if marker in text:
            return semantic("remember", ["attraction"], {"interests": values}, [change("interests", "set", values)])
    raise ValueError(text)


def keep_answer(text: str, state: dict) -> dict:
    if "预算" in text:
        field = "budget"
    elif "人数" in text:
        field = "travelers"
    elif "酒店" in text:
        field = "hotel_area"
    elif "天数" in text:
        field = "days"
    else:
        field = "destination"
    value = state[field]
    return semantic("update", ["general"], {field: value}, [change(field, "keep", value)])


def ambiguous_answer(text: str) -> dict:
    domain = "hotel" if "酒店" in text else "attraction" if "景点" in text or "地方" in text else "itinerary"
    question = {
        "hotel": "请说明需要替换的酒店名称。",
        "attraction": "请说明具体要删除或加入的景点名称。",
        "itinerary": "请说明要调整的具体数值或日期。",
    }[domain]
    return semantic("ask", [domain], {"ambiguous_reference": text}, [], question)


def add_date_cases(splits: dict[str, list[dict]]) -> None:
    cases = [
        ("2026年10月1日出发，10月4日返回。", "2026-10-01", "2026-10-04", 4),
        ("行程改成10月8日至10月12日。", "2026-10-08", "2026-10-12", 5),
        ("十一月三号到，六号返程。", "2026-11-03", "2026-11-06", 4),
        ("下个月10号出发，13号结束。", "2026-10-10", "2026-10-13", 4),
        ("国庆节第一天出发，四号回来。", "2026-10-01", "2026-10-04", 4),
    ]
    for state in CONTEXTS:
        for index, (utterance, start, end, days) in enumerate(cases):
            split = "train" if index < 3 else "dev" if index == 3 else "test"
            entities = {"start_date": start, "end_date": end, "days": days}
            changes = [change("start_date", "set", start), change("end_date", "set", end), change("days", "set", days)]
            splits[split].append(row(state, utterance, semantic("update", ["itinerary"], entities, changes)))


def add_last_value_cases(splits: dict[str, list[dict]]) -> None:
    utterances = [
        ("酒店先改到南山，算了，最终还是住宝安中心。", "hotel_area", "宝安中心"),
        ("预算先定五千，不对，改成六千。", "budget", 6000),
        ("本来想玩三天，最后决定四天。", "days", 4),
        ("先安排四个人，刚确认其实是三个人。", "travelers", 3),
        ("住宿考虑后海，最终确定福田中心区。", "hotel_area", "福田中心区"),
    ]
    state = CONTEXTS[0]
    for index, (utterance, field, value) in enumerate(utterances):
        split = "train" if index < 3 else "dev" if index == 3 else "test"
        domain = "hotel" if field == "hotel_area" else "itinerary" if field == "days" else "general"
        splits[split].append(row(state, utterance, semantic("update", [domain], {field: value}, [change(field, "set", value)])))


def finalize_splits(splits: dict[str, list[dict]]) -> dict[str, list[dict]]:
    seen_all: set[str] = set()
    for split, items in splits.items():
        unique = []
        for item in items:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            assert key not in seen_all, f"duplicate sample across splits: {split}"
            seen_all.add(key)
            unique.append(item)
        random.Random({"train": 20260921, "dev": 20260922, "test": 20260923}[split]).shuffle(unique)
        splits[split] = unique
    return splits


def register(info_path: Path, names_and_files: dict[str, str]) -> None:
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    for name, file_name in names_and_files.items():
        info[name] = {
            "file_name": file_name,
            "formatting": "sharegpt",
            "columns": {"messages": "messages"},
            "tags": {"role_tag": "role", "content_tag": "content", "system_tag": "system", "user_tag": "user", "assistant_tag": "assistant"},
        }
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="/root/autodl-tmp/trippilot-finetune/data")
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    output_dir = data_dir / "semantic_router_v1"
    output_dir.mkdir(parents=True, exist_ok=True)
    splits = build_splits()
    files = {}
    for split, items in splits.items():
        path = output_dir / f"trippilot_semantic_router_v1_{split}.json"
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        files[f"trippilot_semantic_router_v1_{split}"] = f"semantic_router_v1/{path.name}"
        counts = Counter(json.loads(item["messages"][2]["content"])["operation"] for item in items)
        print(f"{split}: {len(items)} samples | " + json.dumps(dict(sorted(counts.items())), ensure_ascii=False))
    register(data_dir / "dataset_info.json", files)
    print("semantic-router-v1 datasets generated and registered")


if __name__ == "__main__":
    main()
