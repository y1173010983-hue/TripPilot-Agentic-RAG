"""Generate error-driven TripPilot preference-understanding SFT examples.

V3 complements the template-expanded V2 dataset.  It targets known failure
classes: no-op updates, negation, multi-field extraction, scope boundaries,
valid JSON and tool-parameter discipline.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


SYSTEM = """领域边界优先：只处理旅游需求及相关追问。非旅游知识、编程、作业、投资等问题必须选 out_of_scope；即使历史有旅游行程，也不能把当前无关问题当旅游问题。混合问题仅处理旅游部分，无关部分不回答。交通、景点、线路、美食、酒店、目的地天气和出行准备属于旅游范围。问候、感谢、询问助手能力属于 social。out_of_scope 时 tools/domains 为空数组、requirement_updates 为空对象、search_query 为空、clarification 为 null。你是旅游对话路由器。只输出 JSON，字段 intent,domains,search_query,requirement_updates,clarification,tools。intent 必须为 out_of_scope/social/qa/explore/compare/plan/modify/format/booking。requirement_updates 只填本轮用户明确更新的字段，允许字段 destination,departure,start_date,end_date,days,travelers,budget,budget_scope,pace,interests,diet,hotel_area,rooms,locked_activities。tools 最多4项，字段name,city,query,origin,destination,date。"""


def tool(name, city="", query="", origin="", destination="", date=""):
    return {"name": name, "city": city, "query": query, "origin": origin, "destination": destination, "date": date}


def decision(intent, domains=None, search_query="", updates=None, clarification=None, tools=None):
    return {"intent": intent, "domains": domains or [], "search_query": search_query,
            "requirement_updates": updates or {}, "clarification": clarification, "tools": tools or []}


def sample(saved, user, output):
    return {"messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "当前已保存需求：" + json.dumps(saved, ensure_ascii=False) + "。本轮用户：" + user},
        {"role": "assistant", "content": json.dumps(output, ensure_ascii=False, separators=(",", ":"))},
    ]}


def build_seed():
    s_hz = {"destination": "杭州", "days": 3, "travelers": 3, "budget": 4000, "hotel_area": "西湖东侧"}
    s_cd = {"destination": "成都", "days": 4, "travelers": 3, "budget": 6000, "hotel_area": "春熙路"}
    s_sz = {"destination": "深圳", "days": 3, "travelers": 2, "budget": 3000, "hotel_area": "福田中心区"}
    rows = []

    # 1. No-op and negation: never write fields the user explicitly keeps unchanged.
    for user in ["预算保持原样，只把行程放慢", "还是三个人，预算也别动", "酒店不换，第二天少安排几个景点", "不要改目的地，只把行程改轻松"]:
        updates = {"pace": "relaxed"} if "行程" in user or "景点" in user else {}
        rows.append(sample(s_hz, user, decision("modify", ["itinerary"], updates=updates)))

    # 2. Counting phrases and family composition.
    for user, updates in [
        ("我们是两大一小，人数还是三位", {}),
        ("现在变成两位大人和一个孩子", {}),
        ("原来两个人，现在两大一小", {"travelers": 3}),
        ("加上一位老人，一共四个人", {"travelers": 4}),
    ]:
        rows.append(sample(s_cd, user, decision("modify", ["travelers"], updates=updates)))

    # 3. Multi-field extraction.
    multi = [
        ("孩子只想看熊猫，吃饭不要辣，酒店继续住春熙路", {"interests": ["熊猫"], "diet": "不吃辣"}, ["attraction", "food"], [tool("poi_search", "成都", "熊猫"), tool("restaurant_search", "成都", "不辣")]),
        ("我妈腿脚不方便，酒店改到机场附近，预算不要变", {"hotel_area": "机场附近", "pace": "relaxed"}, ["hotel", "itinerary"], [tool("hotel_search", "杭州", "机场附近")]),
        ("两个人改成四个人，预算提高到八千，想住宝安中心", {"travelers": 4, "budget": 8000, "hotel_area": "宝安中心"}, ["travelers", "budget", "hotel"], [tool("hotel_search", "深圳", "宝安中心")]),
        ("改成五天游，想多看看博物馆，餐食尽量清淡", {"days": 5, "interests": ["博物馆"], "diet": "清淡"}, ["itinerary", "attraction", "food"], [tool("poi_search", "成都", "博物馆"), tool("restaurant_search", "成都", "清淡")]),
    ]
    for user, updates, domains, tools in multi:
        base = s_cd if "熊猫" in user or "博物馆" in user else s_hz if "我妈" in user else s_sz
        rows.append(sample(base, user, decision("modify", domains, updates=updates, tools=tools)))

    # 4. Explicit scope boundary, including travel history and mixed requests.
    for user in ["解释一下 Python 装饰器", "帮我写一个爬虫", "比特币现在能买吗", "给我做一道高数题", "不用管旅行，讲讲 LangChain 原理", "把刚才的行程代码写成 Python"]:
        rows.append(sample(s_sz, user, decision("out_of_scope", clarification=None)))
    rows.append(sample(s_sz, "顺便解释 Python 装饰器，再推荐福田附近酒店", decision("qa", ["hotel"], "福田附近酒店", tools=[tool("hotel_search", "深圳", "福田")])) )

    # 5. Social and formatting must not mutate requirement memory.
    for user in ["你好", "谢谢，先这样", "你能帮我做什么", "把刚才的行程整理成表格", "把第三天单独列出来"]:
        intent = "format" if "整理" in user or "列出" in user else "social"
        rows.append(sample(s_sz, user, decision(intent, ["itinerary"] if intent == "format" else [])))

    # 6. Tool specificity: weather only, route origin/destination, unavailable booking source.
    rows.extend([
        sample(s_sz, "明天深圳会下雨吗", decision("qa", ["weather"], "深圳天气", tools=[tool("weather_query", "深圳", date="")])) ,
        sample(s_hz, "从萧山机场到西湖东侧怎么去", decision("qa", ["transportation"], "萧山机场到西湖东侧", tools=[tool("route_query", "杭州", origin="萧山机场", destination="西湖东侧")])) ,
        sample(s_cd, "帮我订春熙路附近酒店", decision("booking", ["hotel"], "春熙路酒店", tools=[tool("hotel_inventory", "成都", "春熙路")])) ,
        sample(s_cd, "成都有哪些适合老人的公园", decision("explore", ["attraction"], "成都 老人 公园", tools=[tool("poi_search", "成都", "适合老人 公园")])) ,
    ])

    # 7. Destination switch must discard unrelated prior location choices.
    rows.extend([
        sample(s_sz, "不去深圳了，改去杭州玩四天", decision("modify", ["destination", "itinerary"], updates={"destination": "杭州", "days": 4})),
        sample(s_cd, "目的地换成上海，其他细节以后再说", decision("modify", ["destination"], updates={"destination": "上海"})),
    ])

    # 8. History references, ambiguity and explicit no-plan statements.
    rows.extend([
        sample(s_sz, "第二天那个海边项目先别去了，换成室内的", decision("modify", ["itinerary", "attraction"], "第二天 室内景点", clarification="请确认要替换第二天的哪一个海边项目？", tools=[tool("poi_search", "深圳", "室内景点")])) ,
        sample(s_cd, "酒店换一家，但还是住春熙路附近", decision("modify", ["hotel"], "春熙路附近酒店", tools=[tool("hotel_search", "成都", "春熙路")])) ,
        sample(s_hz, "把前面第二个景点换掉", decision("modify", ["attraction"], clarification="请确认要替换的是哪个景点，以及希望换成什么类型？")) ,
        sample(s_sz, "先记住我不吃辣，但暂时不要给我安排具体行程", decision("qa", ["food"], updates={"diet": "不吃辣"})) ,
        sample(s_hz, "不要重新规划，我只想知道杭州今天的天气", decision("qa", ["weather"], "杭州天气", tools=[tool("weather_query", "杭州")])) ,
    ])

    # 9. Date, room and budget-scope fields are easy to omit or invent.
    rows.extend([
        sample(s_sz, "改成十月一日出发，十月四日回来", decision("modify", ["itinerary"], updates={"start_date": "2026-10-01", "end_date": "2026-10-04", "days": 4})),
        sample(s_cd, "三个人住两间房，预算是全程总预算，不是每人预算", decision("modify", ["hotel", "budget"], updates={"rooms": 2, "budget_scope": "total"})),
        sample(s_hz, "预算四千是两个人总共，不是单人四千", decision("modify", ["budget"], updates={"budget_scope": "total"})),
        sample(s_sz, "预算上限不变，不要擅自帮我提高", decision("qa", ["budget"])),
    ])
    return rows


def expand(rows, total):
    heads = ["", "请注意，", "我想确认一下：", "麻烦按这个要求处理："]
    tails = ["", "。", "，别漏掉我说的条件。", "，按已保存需求理解。"]
    all_rows, seen = [], set()
    for row in rows:
        before, user = row["messages"][1]["content"].split("本轮用户：", 1)
        for head in heads:
            for tail in tails:
                clone = json.loads(json.dumps(row, ensure_ascii=False))
                clone["messages"][1]["content"] = before + "本轮用户：" + head + user + tail
                key = json.dumps(clone, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    all_rows.append(clone); seen.add(key)
    random.Random(20260916).shuffle(all_rows)
    if len(all_rows) < total:
        repeats = (total + len(all_rows) - 1) // len(all_rows)
        all_rows = (all_rows * repeats)[:total]
    return all_rows[:total]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-samples", type=int, default=480)
    args = parser.parse_args()
    data = expand(build_seed(), args.max_samples)
    target = Path(args.output); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"V3 错题样本数：{len(data)}")
    print(f"输出文件：{target}")


if __name__ == "__main__":
    main()
