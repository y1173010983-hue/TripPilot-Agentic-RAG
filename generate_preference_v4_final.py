"""Build the final TripPilot V4 training and held-out acceptance datasets."""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

SYSTEM = """领域边界优先：只处理旅游需求及相关追问。非旅游知识、编程、作业、投资等问题必须选 out_of_scope；即使历史有旅游行程，也不能把当前无关问题当旅游问题。混合问题仅处理旅游部分，无关部分不回答。问候、感谢、询问助手能力属于 social。out_of_scope 时 tools/domains 为空数组、requirement_updates 为空对象、search_query 为空、clarification 为 null。你是旅游对话路由器。只输出 JSON，字段 intent,domains,search_query,requirement_updates,clarification,tools。intent 必须为 out_of_scope/social/qa/explore/compare/plan/modify/format/booking。requirement_updates 只填本轮明确更新的字段，允许字段 destination,departure,start_date,end_date,days,travelers,budget,budget_scope,pace,interests,diet,hotel_area,rooms,locked_activities。tools 最多4项，字段 name,city,query,origin,destination,date。工具名只能是 weather_query,poi_search,restaurant_search,hotel_search,route_query,ticket_search,hotel_inventory,intercity_search。pace 只能用 relaxed 或 compact。interests 和 locked_activities 必须是数组。"""
KEYS = {"intent", "domains", "search_query", "requirement_updates", "clarification", "tools"}


def t(name, city="", query="", origin="", destination="", date=""):
    return {"name": name, "city": city, "query": query, "origin": origin, "destination": destination, "date": date}


def d(intent, domains=(), updates=None, query="", clarification=None, tools=()):
    return {"intent": intent, "domains": list(domains), "search_query": query,
            "requirement_updates": updates or {}, "clarification": clarification, "tools": list(tools)}


def row(saved, utterance, output):
    assert set(output) == KEYS
    return {"messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "当前已保存需求：" + json.dumps(saved, ensure_ascii=False) + "。本轮用户：" + utterance},
        {"role": "assistant", "content": json.dumps(output, ensure_ascii=False, separators=(",", ":"))},
    ]}


SZ = {"destination": "深圳", "days": 3, "travelers": 2, "budget": 3000, "hotel_area": "福田中心区"}
CD = {"destination": "成都", "days": 4, "travelers": 3, "budget": 6000, "hotel_area": "春熙路"}
HZ = {"destination": "杭州", "days": 3, "travelers": 3, "budget": 4000, "hotel_area": "西湖东侧"}


def training_seeds():
    rows = []
    # Canonical pace mapping: diverse wording, one allowed enum.
    for text in ("慢一点安排", "别把每天排太满", "带老人出行，路程少一些", "多留时间休息", "行程宽松些"):
        rows.append(row(HZ, text, d("modify", ["itinerary"], {"pace": "relaxed"})))
    for text in ("时间紧，景点尽量集中", "每天安排紧凑一点", "想在三天里多玩几个地方"):
        rows.append(row(SZ, text, d("modify", ["itinerary"], {"pace": "compact"})))
    # Array type and multi-slot extraction.
    rows += [
        row(CD, "孩子最想看熊猫和三星堆，吃饭不要辣", d("modify", ["attraction", "food"], {"interests": ["熊猫", "三星堆"], "diet": "不吃辣"}, tools=[t("poi_search", "成都", "熊猫 三星堆"), t("restaurant_search", "成都", "不辣")])) ,
        row(SZ, "把世界之窗和欢乐谷锁定，其他景点可以调整", d("modify", ["attraction"], {"locked_activities": ["世界之窗", "欢乐谷"]}, tools=[t("poi_search", "深圳", "世界之窗 欢乐谷")])) ,
        row(HZ, "喜欢西湖、博物馆和慢节奏咖啡馆", d("modify", ["attraction"], {"interests": ["西湖", "博物馆", "咖啡馆"]}, tools=[t("poi_search", "杭州", "西湖 博物馆 咖啡馆")])) ,
        row(CD, "加一位老人和一个孩子，一共五人；房间订三间；口味清淡", d("modify", ["travelers", "hotel", "food"], {"travelers": 5, "rooms": 3, "diet": "清淡"}, tools=[t("hotel_search", "成都", "三间房"), t("restaurant_search", "成都", "清淡")])) ,
    ]
    # No-op updates: output must contain no copied field.
    for text, base in (("预算还是三千，别改费用", SZ), ("仍然两个人出发", SZ), ("继续住福田中心区，不用换酒店", SZ), ("人数不变，还是两大一小", CD), ("不换目的地，杭州照旧", HZ)):
        rows.append(row(base, text, d("qa", ["budget"] if "预算" in text else ["travelers"] if "人" in text else ["hotel"] if "酒店" in text else ["destination"])))
    # Historical reference / ambiguous request.
    rows += [
        row(CD, "酒店换一家，但范围仍然是春熙路周边", d("modify", ["hotel"], query="春熙路酒店", tools=[t("hotel_search", "成都", "春熙路")])) ,
        row(SZ, "第二天那个项目换成室内的", d("modify", ["itinerary", "attraction"], query="深圳 室内景点", clarification="请确认第二天要替换的具体项目。", tools=[t("poi_search", "深圳", "室内景点")])) ,
        row(HZ, "把前面提到的第二个景点删掉", d("modify", ["attraction"], clarification="请确认要删除的是哪个景点。")) ,
    ]
    # Whitelisted tools and exact route parameters.
    rows += [
        row(CD, "从双流机场到春熙路怎么去", d("qa", ["transportation"], query="双流机场到春熙路", tools=[t("route_query", "成都", origin="双流机场", destination="春熙路")])) ,
        row(SZ, "明天深圳天气怎么样", d("qa", ["weather"], query="深圳天气", tools=[t("weather_query", "深圳")])) ,
        row(CD, "给孩子找能看熊猫的地方", d("explore", ["attraction"], query="成都 熊猫", tools=[t("poi_search", "成都", "熊猫")])) ,
        row(CD, "春熙路附近有没有不辣的川菜", d("qa", ["food"], query="春熙路 不辣 川菜", tools=[t("restaurant_search", "成都", "春熙路 不辣 川菜")])) ,
        row(SZ, "帮我预订宝安中心的酒店", d("booking", ["hotel"], query="宝安中心酒店", tools=[t("hotel_inventory", "深圳", "宝安中心")])) ,
    ]
    # Scope boundary and mixed requests.
    for text in ("Python 的生成器是什么", "给我写一个爬虫", "黄金现在适合投资吗", "帮我完成数据结构作业", "解释一下 RAG 的重排序"):
        rows.append(row(SZ, text, d("out_of_scope")))
    rows += [
        row(SZ, "顺便讲讲 Python 装饰器，再查福田附近的酒店", d("qa", ["hotel"], query="福田附近酒店", tools=[t("hotel_search", "深圳", "福田")])) ,
        row(CD, "记住我不能吃辣，先别开始制定行程", d("qa", ["food"], {"diet": "不吃辣"})) ,
        row(SZ, "不去深圳了，改到杭州玩四天", d("modify", ["destination", "itinerary"], {"destination": "杭州", "days": 4})) ,
        row(HZ, "十月一日出发，十月四日回来，预算仍是总共四千", d("modify", ["itinerary", "budget"], {"start_date": "2026-10-01", "end_date": "2026-10-04", "days": 4, "budget_scope": "total"})) ,
    ]
    return rows


def test_seeds():
    return [
        row(HZ, "我爸妈同行，不想走太多路，酒店挪到离机场近的区域", d("modify", ["hotel", "itinerary"], {"hotel_area": "机场附近", "pace": "relaxed"}, tools=[t("hotel_search", "杭州", "机场附近")])) ,
        row(CD, "现在还是两大一小，熊猫和博物馆都想去，菜不要辣", d("modify", ["attraction", "food"], {"interests": ["熊猫", "博物馆"], "diet": "不吃辣"}, tools=[t("poi_search", "成都", "熊猫 博物馆"), t("restaurant_search", "成都", "不辣")])) ,
        row(SZ, "预算不调整，只想把活动节奏改得轻松", d("modify", ["itinerary"], {"pace": "relaxed"})) ,
        row(SZ, "告诉我快速排序的实现方式", d("out_of_scope")),
        row(CD, "换个酒店就行，仍在春熙路活动", d("modify", ["hotel"], query="春熙路酒店", tools=[t("hotel_search", "成都", "春熙路")])) ,
        row(HZ, "去掉上次提到的那个景点", d("modify", ["attraction"], clarification="请确认要删除的景点名称。")),
        row(SZ, "两个人订两间房，饮食要素食", d("modify", ["hotel", "food"], {"rooms": 2, "diet": "素食"}, tools=[t("hotel_search", "深圳", "两间房"), t("restaurant_search", "深圳", "素食")])) ,
        row(SZ, "你好，能做哪些旅行帮助", d("social")),
    ]


def expand(seeds, total, heads, tails):
    result, seen = [], set()
    for item in seeds:
        prefix, text = item["messages"][1]["content"].split("本轮用户：", 1)
        for head in heads:
            for tail in tails:
                clone = json.loads(json.dumps(item, ensure_ascii=False))
                clone["messages"][1]["content"] = prefix + "本轮用户：" + head + text + tail
                key = json.dumps(clone, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    result.append(clone); seen.add(key)
    random.Random(20260916).shuffle(result)
    return (result * ((total + len(result) - 1) // len(result)))[:total]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-output", required=True)
    parser.add_argument("--test-output", required=True)
    args = parser.parse_args()
    train = expand(training_seeds(), 720, ["", "请按历史需求理解：", "麻烦处理：", "我想调整：", "请仔细识别："], ["", "。", "，不要遗漏条件。", "，请严格按协议输出。", "，不要修改未要求变动的信息。"])
    test = expand(test_seeds(), 100, ["", "我想确认：", "请严格处理：", "请根据已有条件判断：", "这次需求是："], ["", "。", "，不要猜测未说出的条件。", "，只返回协议 JSON。", "，请避免无意义更新。"])
    for path, data in ((Path(args.train_output), train), (Path(args.test_output), test)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"V4 训练样本：{len(train)}")
    print(f"独立验收样本：{len(test)}")


if __name__ == "__main__":
    main()
