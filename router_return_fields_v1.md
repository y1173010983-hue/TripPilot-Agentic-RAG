# 路由返回字段结构（来自项目真实 IntentDecision）

来源：

- `backend/trippilot/retrieval/module5_agent_workflow.py`
- `backend/trippilot/retrieval/module4_travel_tools.py`

## 1. 根对象

模型路由只输出一个 JSON 根对象，顶层字段固定为 6 个，不能增加其他字段：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `intent` | string | `"qa"` | 意图枚举，见下方列表 |
| `domains` | array | `[]` | 本轮涉及领域，当前 Pydantic 只约束为字符串数组，不对内容做硬校验 |
| `search_query` | string | `""` | 用于知识库检索的独立问句 |
| `requirement_updates` | object | `{}` | 本轮明确更新的旅行需求字段 |
| `clarification` | string / null | `null` | 需要追问时的中文短句，无需追问时为 `null` |
| `tools` | array | `[]` | 实时工具请求，最多 4 项，每项结构见下方 |

## 2. intent 枚举

项目 Pydantic 固化为 9 个值：

```text
out_of_scope
social
qa
explore
compare
plan
modify
format
booking
```

## 3. domains 说明

`domains` 在代码里是 `list[str]`，没有 Literal 枚举。为了让训练数据稳定，训练时仍建议使用固定领域集合：

```text
attraction
hotel
food
transportation
itinerary
weather
budget
travelers
dates
general
```

这不是代码强制约束，而是训练协议约束。

## 4. requirement_updates 可填字段

项目在 `step0_normalize_and_validate_decision` 中校验，只允许这些字段：

```text
destination
departure
start_date
end_date
days
travelers
budget
budget_scope
pace
interests
diet
hotel_area
rooms
locked_activities
```

其中几个关键格式：

- `pace`：只能是 `relaxed` 或 `compact`。
- `interests`、`locked_activities`：必须是非空字符串数组。
- `days`、`travelers`、`rooms`：必须是正整数。
- `budget`：必须是非负数字。
- `start_date`、`end_date`：必须是 `YYYY-MM-DD`。

## 5. tools 数组中的 ToolRequest

`tools` 中每一项固定为 6 个字段：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `name` | string | 必填 | 工具枚举，见下方列表 |
| `city` | string | `""` | 城市名 |
| `query` | string | `""` | 查询关键词 |
| `origin` | string | `""` | 路线起点 |
| `destination` | string | `""` | 路线终点 |
| `date` | string | `""` | 日期，如 `YYYY-MM-DD` |

`name` 枚举：

```text
weather_query
poi_search
restaurant_search
hotel_search
route_query
ticket_search
hotel_inventory
intercity_search
```

## 6. 一个完整示例 JSON

```json
{
  "intent": "modify",
  "domains": ["hotel", "itinerary"],
  "search_query": "",
  "requirement_updates": {
    "hotel_area": "宝安中心",
    "pace": "relaxed"
  },
  "clarification": null,
  "tools": [
    {
      "name": "hotel_search",
      "city": "深圳",
      "query": "宝安中心附近",
      "origin": "",
      "destination": "",
      "date": ""
    }
  ]
}
```

## 7. 与内部状态返回值区分

上面是“模型路由输出”的字段结构。流程内 `step1_understand_request` 返回的 `AgentState` 还会包含 `run_id, session_id, context, decision, requirements, evidence, tool_results, plan, issues, revision, answer` 等字段，那是内部流水线状态，不属于模型路由输出。
