# TripPilot 微调协议（草案 v1）

本协议只允许模型输出原项目真实使用的 `IntentDecision`，不再引入 `operation`、`entities`、`state_changes`、`replacement` 等额外字段。

## 1. 顶层字段

模型只输出一个 JSON 对象，字段固定为：

```json
{
  "intent": "qa",
  "domains": [],
  "search_query": "",
  "requirement_updates": {},
  "clarification": null,
  "tools": []
}
```

字段含义：

- `intent`：本轮用户请求的意图，从九种枚举中选一个。
- `domains`：本轮涉及的领域，从领域白名单中选，可空数组。
- `search_query`：用于知识库检索的独立问句，不需要检索时为空字符串。
- `requirement_updates`：本轮用户明确更新或新增的旅行需求字段，字段名必须属于需求白名单。
- `clarification`：需要向用户追问时写中文短句；无需追问时为 `null`。
- `tools`：需要实时查询时最多 4 项工具请求，字段固定为工具白名单格式。

## 2. intent 枚举

模型只能从以下九个值中选择：

| intent | 使用场景 |
|---|---|
| `out_of_scope` | 与旅游无关的问题：编程、金融、作业、闲聊无关话题等 |
| `social` | 问候、感谢、询问助手能力，无实质旅游需求 |
| `qa` | 直接问答、查询信息、保存偏好但不要生成行程 |
| `explore` | 探索推荐：找景点、美食、酒店等 |
| `compare` | 比较多个方案或酒店/景点/餐厅 |
| `plan` | 制定完整行程方案 |
| `modify` | 修改已保存行程或旅行条件 |
| `format` | 把已有内容整理、格式化、翻译为可读文案 |
| `booking` | 预订酒店、票务等（本项目只做预订引导） |

## 3. domains 领域白名单

`domains` 只能包含以下值，不能新增：

```text
attraction, hotel, food, transportation, itinerary, weather, budget, travelers, dates, general
```

## 4. requirement_updates 需求字段白名单

只能填写以下字段：

```text
destination, departure, start_date, end_date, days, travelers,
budget, budget_scope, pace, interests, diet, hotel_area, rooms, locked_activities
```

取值约束：

- `pace` 只能是 `relaxed` 或 `compact`，不能输出“轻松”“紧凑”等中文。
- `interests`、`locked_activities` 必须是字符串数组。
- `days`、`travelers`、`rooms` 必须是正整数。
- `budget` 必须是非负数字。
- `start_date`、`end_date` 必须是 `YYYY-MM-DD`。
- 只填本轮用户明确更新的字段，不能把历史已有字段重复输出。

## 5. tools 工具白名单

`tools` 中每个对象只能使用以下工具名，且字段固定为 `name, city, query, origin, destination, date`：

```text
weather_query, poi_search, restaurant_search, hotel_search,
route_query, ticket_search, hotel_inventory, intercity_search
```

工具必填约束：

| 工具 | 必填参数 |
|---|---|
| `weather_query` | `city` |
| `poi_search` | `query` |
| `restaurant_search` | `query` |
| `hotel_search` | `query` |
| `route_query` | `origin`、`destination` |
| `ticket_search` | 可选，项目只做预订引导 |
| `hotel_inventory` | 可选，项目只做预订引导，`query` 可保留关键词 |
| `intercity_search` | 可选，项目未配置供应商 |

## 6. 固定工具选择参考

| 用户需求 | 建议 intent | 建议 domains | 建议 tools |
|---|---|---|---|
| 查询天气 | `qa` | `weather` | `weather_query` |
| 搜索景点 | `explore` | `attraction` | `poi_search` |
| 搜索餐厅/美食 | `explore` | `food` | `restaurant_search` |
| 搜索酒店 | `explore` | `hotel` | `hotel_search` |
| 查询路线 | `qa` | `transportation` | `route_query` |
| 预订引导 | `booking` | 对应领域 | `ticket_search`/`hotel_inventory` |
| 仅保存偏好，不安排行程 | `qa` | 对应领域 | 无工具 |
| 修改完整行程 | `modify` | `itinerary` 等 | 按需选择工具 |
| 无关问题 | `out_of_scope` | `[]` | 无工具 |

## 7. 禁止行为

- 不得输出协议以外的顶层字段。
- 不得输出协议以外的需求字段。
- 不得输出协议以外的工具名。
- `out_of_scope` 时 `tools`、`domains` 必须为空数组，`search_query` 为空，`clarification` 为 `null`，`requirement_updates` 为空对象。
- 无关问题不得进入记忆，因此 `requirement_updates` 必须为空。

## 8. 典型示例

### 示例 1：天气查询

```json
{
  "intent": "qa",
  "domains": ["weather"],
  "search_query": "深圳未来三天天气",
  "requirement_updates": {},
  "clarification": null,
  "tools": [
    {
      "name": "weather_query",
      "city": "深圳",
      "query": "",
      "origin": "",
      "destination": "",
      "date": ""
    }
  ]
}
```

### 示例 2：搜索酒店

```json
{
  "intent": "explore",
  "domains": ["hotel"],
  "search_query": "深圳福田性价比高的酒店",
  "requirement_updates": {},
  "clarification": null,
  "tools": [
    {
      "name": "hotel_search",
      "city": "深圳",
      "query": "福田 性价比高",
      "origin": "",
      "destination": "",
      "date": ""
    }
  ]
}
```

### 示例 3：酒店预订引导

```json
{
  "intent": "booking",
  "domains": ["hotel"],
  "search_query": "",
  "requirement_updates": {
    "hotel_area": "宝安中心"
  },
  "clarification": "酒店预订需前往官方平台完成",
  "tools": [
    {
      "name": "hotel_inventory",
      "city": "深圳",
      "query": "宝安中心",
      "origin": "",
      "destination": "",
      "date": ""
    }
  ]
}
```

### 示例 4：酒店换一家但区域不变

```json
{
  "intent": "qa",
  "domains": ["hotel"],
  "search_query": "",
  "requirement_updates": {},
  "clarification": "您希望换一家福田区的酒店，我可以重新推荐几家。",
  "tools": [
    {
      "name": "hotel_search",
      "city": "深圳",
      "query": "福田区其他酒店",
      "origin": "",
      "destination": "",
      "date": ""
    }
  ]
}
```

### 示例 5：只保存偏好，不安排行程

```json
{
  "intent": "qa",
  "domains": ["food"],
  "search_query": "",
  "requirement_updates": {
    "diet": "不吃辣"
  },
  "clarification": null,
  "tools": []
}
```

### 示例 6：无关问题

```json
{
  "intent": "out_of_scope",
  "domains": [],
  "search_query": "",
  "requirement_updates": {},
  "clarification": null,
  "tools": []
}
```

### 示例 7：歧义追问

```json
{
  "intent": "qa",
  "domains": ["attraction"],
  "search_query": "",
  "requirement_updates": {},
  "clarification": "您说的‘海边’是指深圳大梅沙、小梅沙还是其他海边景区？",
  "tools": []
}
```

## 9. 训练与验收对齐

- 训练提示词必须复制原项目 `module5_agent_workflow.py` 第 174-195 行的核心约束。
- 验收脚本只校验本协议字段、枚举、白名单和必填参数。
- 训练数据、训练提示词、验收脚本三者使用同一份协议，不能临时新增字段。
