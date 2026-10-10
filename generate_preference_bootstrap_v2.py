"""Generate a bootstrap SFT dataset for TripPilot preference understanding.

This script deliberately follows the backend IntentDecision contract.  It is a
pipeline-training dataset, not a substitute for labelled production dialogs.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any


SYSTEM = """你是 TripPilot 旅游需求理解器。根据当前对话和已保存需求，只输出一个合法的 IntentDecision JSON。
intent 只能是 out_of_scope、social、qa、explore、compare、plan、modify、format、booking。
requirement_updates 只能更新 destination、departure、start_date、end_date、days、travelers、budget、budget_scope、pace、interests、diet、hotel_area、rooms、locked_activities。
tools 中每个工具对象必须包含 name、city、query、origin、destination、date。禁止编造字段，禁止输出解释文字。"""

FIELDS = (
    "destination", "departure", "start_date", "end_date", "days", "travelers",
    "budget", "budget_scope", "pace", "interests", "diet", "hotel_area", "rooms",
    "locked_activities",
)

CITIES = [
    ("深圳", ["福田中心区", "宝安中心", "南山后海"], ["海边", "科技馆", "亲子乐园"]),
    ("北京", ["东直门", "王府井", "国贸"], ["故宫", "博物馆", "胡同"]),
    ("成都", ["春熙路", "宽窄巷子", "高新区"], ["大熊猫", "川菜", "茶馆"]),
    ("上海", ["人民广场", "静安寺", "陆家嘴"], ["外滩", "美术馆", "迪士尼"]),
    ("杭州", ["西湖东侧", "钱江新城", "武林广场"], ["西湖", "宋韵", "龙井"]),
]


def step1_tool(name: str, city: str = "", query: str = "", origin: str = "", destination: str = "", date: str = "") -> dict[str, str]:
    return {"name": name, "city": city, "query": query, "origin": origin, "destination": destination, "date": date}


def step2_decision(intent: str, *, domains: list[str], search_query: str = "", updates: dict[str, Any] | None = None,
                   clarification: str = "", tools: list[dict[str, str]] | None = None) -> dict[str, Any]:
    clean_updates = {key: value for key, value in (updates or {}).items() if key in FIELDS}
    return {
        "intent": intent,
        "domains": domains,
        "search_query": search_query,
        "requirement_updates": clean_updates,
        "clarification": clarification,
        "tools": tools or [],
    }


def step3_message(user_text: str, decision: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
    saved_text = json.dumps(saved, ensure_ascii=False, separators=(",", ":"))
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"当前已保存需求：{saved_text}。本轮用户：{user_text}"},
            {"role": "assistant", "content": json.dumps(decision, ensure_ascii=False, separators=(",", ":"))},
        ]
    }


def step4_examples() -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    hotel_phrases = ["酒店换到{area}附近", "住在{area}方便吗，帮我改过去", "把住宿区域调整为{area}"]
    budget_phrases = ["总预算改成{budget}元", "预算控制在{budget}块以内", "这次最多花{budget}元"]
    pace_phrases = ["行程安排轻松一点", "别排太满，节奏放慢些", "每天少走一点路"]
    for city, areas, interests in CITIES:
        base = {"destination": city, "days": 3, "travelers": 2, "budget": 3000, "hotel_area": areas[0]}
        for area in areas:
            for phrase in hotel_phrases:
                text = phrase.format(area=area)
                examples.append(step3_message(text, step2_decision("modify", domains=["hotel"], updates={"hotel_area": area}, tools=[step1_tool("hotel_search", city=city, query=area)]), base))
        for budget in (1800, 2500, 4500, 6000, 8000):
            for phrase in budget_phrases:
                text = phrase.format(budget=budget)
                examples.append(step3_message(text, step2_decision("modify", domains=["budget"], updates={"budget": budget}), base))
        for phrase in pace_phrases:
            examples.append(step3_message(phrase, step2_decision("modify", domains=["itinerary"], updates={"pace": "relaxed"}), base))
        for travelers, text in ((1, "改成一个人出行"), (3, "现在有三个人一起去"), (4, "同行人数改为4人，其中有一位老人"), (2, "还是两个人，不用改人数")):
            updates = {} if travelers == 2 else {"travelers": travelers}
            examples.append(step3_message(text, step2_decision("modify", domains=["travelers"], updates=updates), base))
        for days, text in ((2, "时间不够，压缩到两天"), (4, "多留一天，改成四日游"), (5, "我有五天时间，慢慢玩")):
            examples.append(step3_message(text, step2_decision("modify", domains=["itinerary"], updates={"days": days}), base))
        for interest in interests:
            examples.append(step3_message(f"我更想去{interest}相关的地方", step2_decision("modify", domains=["attraction"], updates={"interests": [interest]}, tools=[step1_tool("poi_search", city=city, query=interest)]), base))
        for diet in ("不吃辣", "想吃当地小吃", "有素食需求"):
            examples.append(step3_message(f"饮食方面我{diet}", step2_decision("modify", domains=["food"], updates={"diet": diet}, tools=[step1_tool("restaurant_search", city=city, query=diet)]), base))
        examples.extend([
            step3_message(f"{city}这几天会下雨吗", step2_decision("qa", domains=["weather"], search_query=f"{city}天气", tools=[step1_tool("weather_query", city=city)]), base),
            step3_message(f"从机场去{areas[0]}怎么走", step2_decision("qa", domains=["transportation"], search_query=f"机场到{areas[0]}", tools=[step1_tool("route_query", city=city, origin="机场", destination=areas[0])]), base),
            step3_message(f"{city}有哪些适合带老人去的景点", step2_decision("explore", domains=["attraction"], search_query=f"{city} 老人 景点", tools=[step1_tool("poi_search", city=city, query="适合老人 景点")]), base),
            step3_message(f"帮我比较{areas[0]}和{areas[-1]}住哪里更方便", step2_decision("compare", domains=["hotel"], search_query=f"{areas[0]} {areas[-1]} 住宿交通", tools=[step1_tool("hotel_search", city=city, query=f"{areas[0]} {areas[-1]}")]), base),
            step3_message(f"两个人去{city}三天，预算5000，帮我重新规划", step2_decision("plan", domains=["itinerary", "hotel", "attraction"], updates={"travelers": 2, "days": 3, "budget": 5000}, tools=[step1_tool("hotel_search", city=city), step1_tool("poi_search", city=city, query="热门景点")]), base),
            step3_message(f"酒店换到{areas[-1]}，预算不变，第二天安排轻松一点", step2_decision("modify", domains=["hotel", "itinerary"], updates={"hotel_area": areas[-1], "pace": "relaxed"}, tools=[step1_tool("hotel_search", city=city, query=areas[-1])]), base),
        ])
    examples.extend([
        step3_message("什么是 LangChain", step2_decision("out_of_scope", domains=[]), {}),
        step3_message("帮我写一段 Python 排序代码", step2_decision("out_of_scope", domains=[]), {}),
        step3_message("你好", step2_decision("social", domains=[]), {}),
        step3_message("谢谢，先这样", step2_decision("social", domains=[]), {}),
        step3_message("把刚才的行程整理成表格", step2_decision("format", domains=["itinerary"]), {"destination": "成都", "days": 3}),
        step3_message("帮我订春熙路附近的酒店", step2_decision("booking", domains=["hotel"], search_query="春熙路 酒店", tools=[step1_tool("hotel_inventory", city="成都", query="春熙路")]), {"destination": "成都", "days": 3}),
    ])
    return examples


def step5_expand_examples(seed: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    prefixes = ["", "麻烦你，", "请帮我，", "我想调整一下："]
    suffixes = ["", "，谢谢", "，优先方便出行", "，按这个要求更新"]
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in seed:
        source = item["messages"][1]["content"]
        marker = "本轮用户："
        before, user_text = source.split(marker, 1)
        for prefix in prefixes:
            for suffix in suffixes:
                clone = json.loads(json.dumps(item, ensure_ascii=False))
                clone["messages"][1]["content"] = before + marker + prefix + user_text + suffix
                key = json.dumps(clone, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    output.append(clone)
    random.Random(20260916).shuffle(output)
    if len(output) < limit:
        raise RuntimeError(f"样本不足：仅生成 {len(output)} 条")
    return output[:limit]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="output JSON file")
    parser.add_argument("--max-samples", type=int, default=1500)
    args = parser.parse_args()
    dataset = step5_expand_examples(step4_examples(), args.max_samples)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成样本数：{len(dataset)}")
    print(f"输出文件：{target}")


if __name__ == "__main__":
    main()
