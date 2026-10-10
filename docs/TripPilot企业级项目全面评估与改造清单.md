# TripPilot 企业级改造前后对比

> 阅读方法：每一项先看“业务例子”，了解用户实际会遇到什么问题，再看代码位置和修改前后流程。

---

## 1. 总业务流程：所有修改位于哪里

```text
用户或管理员
      │
      ▼
① 接口与权限
      │
      ▼
② 任务队列
      │
      ├───────────────┐
      ▼               ▼
③ 文档存库         ④ 对话 Agent
      │               │
      ▼               ├──⑤ 记忆
PostgreSQL            ├──⑥ 知识检索 ──► Milvus
MinIO                 ├──⑦ 天气/酒店/交通
Milvus                └──⑧ 大模型回答
                              │
                              ▼
                       ⑨ SSE 与前端

⑩ 数据库迁移、⑪ 监控、⑫ 测试部署贯穿整个流程
```

### 必须先改的顺序

```text
第一步：⑫ 测试隔离
第二步：② 队列、租约和重试
第三步：⑥ 检索权限下推
第四步：⑩ 数据库迁移
第五步：① 认证、限流和密钥
```

---

# ① 接口与权限

**业务位置**

```text
用户请求 ->【接口与权限】-> 业务系统
```

**代码位置**

- `backend/trippilot/auth.py`
- `backend/trippilot/app.py`
- `frontend/src/stores/auth.ts`
- `frontend/src/router.ts`

### 业务例子

> 公司员工小王离职了，但他之前登录获得的 JWT 还可以继续使用约 12 小时。他仍然能够从其他电脑访问旅游知识库。管理员目前无法立即让这个令牌失效。

改造后，管理员禁用小王账号时，他的 refresh token 会立即失效；短期 access token 最多再存活 15～30 分钟。企业接入 SSO 后，员工离职也可以由企业身份系统统一停止访问。

### 修改前

```text
公开注册
   -> 用户登录
   -> 获得一个约 12 小时有效的 JWT
   -> JWT 无法主动撤销
   -> 接口没有完整的登录、聊天、上传限流
```

### 修改后

```text
企业邀请或 SSO
   -> 用户登录
   -> 15～30 分钟 access token
   -> refresh token 单次轮换
   -> 支持注销、账号禁用、令牌撤销
   -> 登录、聊天、上传分别限流
   -> 管理操作写入审计日志
```

**改造作用：** 防止暴力登录、令牌长期失效不了、未知用户注册和管理员操作不可追溯。

**还要处理：** 已经出现在交互内容中的大模型 Key 必须轮换，生产环境改用 Secret Manager 注入。

---

# ② 任务队列

**业务位置**

```text
接口 ->【任务队列】-> 聊天 / 文档处理 / 删除维护
```

**代码位置**

- `backend/trippilot/worker.py::claim_work`
- `backend/trippilot/worker.py::process_chat`
- `backend/trippilot/worker.py::run_one`
- `backend/trippilot/celery_app.py`

### 业务例子

> 上午 10 点，管理员上传了一份 500 页的《全国旅游攻略》。文档正在解析和生成向量时，用户小李询问“帮我规划深圳三日游”。

**现在的结果：** 文档处理和聊天共用 worker。500 页文档可能长时间占用执行位置，小李的聊天任务只能排在后面，页面一直显示处理中。

**改造后的结果：** 文档进入 `ingestion` 队列，小李的问题进入 `chat` 队列，两类任务由不同 worker 同时处理。即使文档处理失败，也不会影响聊天回答。

另一个例子：如果大模型卡住超过 10 分钟，现在的任务租约可能过期并被第二个 worker 再次领取，产生两个回答；加入心跳后，同一个任务只会由一个 worker 执行。

### 修改前

```text
                 ┌-> 聊天任务
同一个队列 ------├-> 文档解析和向量化
                 └-> 删除和清理

任务租约固定 10 分钟
只在部分流式阶段续租
attempts 只累加，没有重试上限和死信
```

### 修改后

```text
chat 队列 ---------> chat worker
ingestion 队列 ----> ingestion worker
embedding 队列 ----> GPU worker
maintenance 队列 --> maintenance worker

每个运行中任务每 30～60 秒续租
失败 -> 判断能否重试 -> 指数退避 -> 超过上限进入死信
取消 -> 传递到模型和外部工具 -> 立即停止
```

**改造作用：** 文档上传不再阻塞聊天；模型调用过慢不会造成任务被重复执行；失败任务不会无限循环。

**需要新增字段**

```text
work_items.max_attempts
work_items.next_retry_at
work_items.last_heartbeat_at
work_items.error_code
work_items.dead_lettered_at
```

---

# ③ 文档存库

**业务位置**

```text
管理员上传 ->【文档存库】-> 可被用户检索的旅游知识
```

**代码位置**

- `ingestion/module4_admin_api.py::upload_document`
- `ingestion/module2_parse_and_chunk.py::step1_parse_document`
- `ingestion/module2_parse_and_chunk.py::step2_split_into_citable_chunks`
- `ingestion/module3_index_and_publish.py::process_document_job`
- `retrieval/module2_knowledge_search.py::index_version`

### 业务例子

> 管理员拖入一份扫描版《深圳景区手册.pdf》。这份 PDF 每一页都是图片，没有可直接复制的文字。

**现在的结果：** 系统可能解析不到正文，最后生成 0 个或很少的分块。管理员看见任务完成，却不知道知识库实际上没有可用内容。

**改造后的结果：** 系统识别出扫描件并执行 OCR，展示“共 86 页、生成 240 个分块、OCR 平均置信度 96%”。管理员预览分块后再点击发布。用户随后询问景区信息时才能检索到这份资料。

### 修改前

```text
上传文件
 -> 检查扩展名、文件头和大小
 -> 解析文本
 -> 固定 600 字分块，重叠 80 字
 -> 生成向量
 -> 写入 Milvus
 -> 自动激活 READY 版本
```

### 修改后

```text
上传文件
 -> MIME 检测、病毒扫描、隔离
 -> PDF 文本解析；扫描件自动 OCR
 -> 按标题、段落、表格和 token 分块
 -> 检查空文本、乱码、重复率和分块质量
 -> 批量生成向量
 -> 管理员预览分块
 -> 审核发布
 -> 支持版本回滚和旧向量回收
```

**改造作用：** 防止恶意文件和低质量内容污染知识库；扫描 PDF 可以入库；发布错误版本时可以回滚。

---

# ④ 对话 Agent

**业务位置**

```text
用户问题 ->【理解意图和编排】-> 检索 / 工具 / 规划 / 回答
```

**代码位置**

- `retrieval/module5_agent_workflow.py::step1_understand_request`
- `retrieval/module5_agent_workflow.py::step2_retrieve_evidence_and_tools`
- `retrieval/module5_agent_workflow.py::step3_build_or_revise_plan`
- `retrieval/module5_agent_workflow.py::step4_validate_time_and_budget`
- `retrieval/module5_agent_workflow.py::make_answer_node`

### 业务例子

> 用户说：“我住深圳宝安中心，上午去大鹏所城，中午回酒店休息，下午两点去世界之窗。”

**现在的结果：** 行程可能通过基础时间校验，因为各活动时间没有直接重叠。但宝安到大鹏往返需要较长交通时间，下午两点到世界之窗在现实中很难完成。

**改造后的结果：** Agent 调用路线工具计算地点之间的交通时间，发现行程不可行，自动调整为“第一天宝安和世界之窗，第二天大鹏所城”，并向用户说明调整原因。

### 修改前

```text
用户问题
 -> 主要由大模型判断 plan / modify / explore / weather 等意图
 -> 调用检索和工具
 -> 生成行程
 -> 只校验时间重叠、天数和基础预算
```

### 修改后

```text
用户问题
 -> 问候、天气、越域、取消等确定场景先走规则
 -> 模糊或复合需求再交给语义路由
 -> 调用检索和工具
 -> 生成行程
 -> 校验路程、营业时间、地理顺序、入住退房、用餐休息、币种和预算
 -> 校验失败时定向修订
```

**改造作用：** 减少路由错误和模型调用时间，生成的旅游线路更接近现实可执行行程。

**必须补充：** 为 plan、modify、explore、weather、hotel、qa、out_of_scope 建立正例、反例和冲突样例测试集。

---

# ⑤ 对话记忆

**业务位置**

```text
历史对话 ->【记忆加载】-> Agent 理解本轮问题
```

**代码位置**

- `retrieval/module3_context_memory.py::step1_load_conversation_context`

- `retrieval/module3_context_memory.py::step2_merge_explicit_requirements`

- `retrieval/module3_context_memory.py::step3_is_destination_switch`

- `retrieval/module3_context_memory.py::step4_rebuild_requirements_after_destination_switch`

  ![image-20260923211200047](C:\Users\86132\AppData\Roaming\Typora\typora-user-images\image-20260923211200047.png)

### 业务例子

> 用户先说：“一家三口去上海，预算 6000 元，不吃辣。”聊了十几轮后又说：“计划有变，改去成都，预算提高到 8000 元。”

**现在的结果：** 较早内容主要按字符摘录。摘要中可能同时出现上海、成都、6000 元和 8000 元，模型有机会把旧目的地或旧预算带入新行程。

**改造后的结果：** 记忆明确保存“成都替代上海”“当前预算为 8000 元”“不吃辣仍然有效”，并记录每个事实来自哪条用户消息。之后用户问“给我安排酒店”时，只会按成都和新预算查询。

### 修改前

```text
近期消息：按字符数截取
较早消息：每条取前 200 字，累计约 12000 字
需求：保存最终 requirements JSON
偏好：任意 key/value
行程：保存版本
```

### 修改后

```text
近期消息：按模型 token 预算保留
较早消息：后台异步生成结构化语义摘要
            ├-> 已确认事实
            ├-> 用户已经做出的决定
            ├-> 已否定选项
            └-> 尚未解决的问题

每个需求事实保存：来源消息、更新时间、置信度和状态
偏好使用受控类型，并允许用户查看、修改、删除和设置有效期
```

**改造作用：** 长对话不会只剩零散摘录；用户修改目的地、日期或预算时，可以正确替换旧需求并追溯来源。

### 结构化摘要字段是什么意思

结构化摘要只保存长对话的过程信息，不替代 `requirements` 中的当前旅游参数。

```json
{
  "confirmed_facts": [],
  "decisions": [],
  "rejected_options": [],
  "open_questions": []
}
```

- `confirmed_facts`：用户已经明确确认、目前仍然有效的事实。例如“2 名成人和 1 名儿童同行”“其中一位老人行动不便”。目的地、日期、预算等标准字段仍以 `requirements` 为准，这里只记录对理解对话有帮助的补充事实。
- `decisions`：对话中已经做出的选择以及选择原因。例如“住宿选择春熙路，因为用户希望靠近地铁”“第二天安排熊猫基地”。
- `rejected_options`：用户明确否定、后续不应再次推荐的方案。例如“不住机场附近”“不安排酒吧”“已放弃上海路线”。
- `open_questions`：继续完成行程前仍需确认的问题。例如“尚未确认返程时间”“还需要确认儿童年龄”。问题得到回答后，应从这里删除并写入对应的需求或事实。

摘要还需要保存以下管理信息，但这些信息不发送给用户：

```json
{
  "summary_through_seq": 36,
  "generated_at": "2026-09-23T15:20:00+08:00",
  "summary_version": 2,
  "source_message_ids": ["msg_1", "msg_2"]
}
```

- `summary_through_seq`：摘要已经处理到第几条消息，避免下一次重复压缩。
- `generated_at`：摘要生成时间。
- `summary_version`：摘要结构或 Prompt 的版本，用于后续升级。
- `source_message_ids`：摘要来源消息，管理员排查或用户更正时可以回到原文。

### 摘要什么时候生成

**当前代码：** `step1_load_conversation_context` 在用户发起下一次有效对话、加载上下文时执行。如果历史消息超过 `context_char_budget`，超出近期窗口的消息会被截取前 200 字并同步追加到 `ChatSession.summary`。当前不调用大模型，也不是在上一轮结束后生成。

**改造后的触发时机：**

```text
一轮有效旅游对话完成
    ↓
保存用户消息和助手完整回答
    ↓
计算尚未摘要的有效消息 token 数
    ├─ 没达到阈值：不生成摘要
    └─ 达到阈值：投递 summary_update 后台任务
                         ↓
                   大模型生成结构化摘要
                         ↓
                   校验 JSON 和来源消息
                         ↓
                   原子更新摘要和 summary_through_seq
```

推荐触发条件：尚未摘要的有效消息达到 **8～12 轮**，或者其 token 数达到 **5000～6000**，满足一个条件即可。最近 **6～10 轮**继续保留完整原文，不进入摘要。

只处理 `completed` 且属于旅游业务的消息；越域拒答、取消、失败和空回答不进入摘要。摘要任务异步运行，不阻塞当前回答。如果下一条用户消息到来时任务尚未完成，系统继续使用旧摘要加未摘要原文，不需要等待。

---

# ⑥ 知识检索

**业务位置**

```text
Agent 查询 ->【知识检索】-> 管理员上传资料中的证据
```

**代码位置**

- `retrieval/module2_knowledge_search.py::step1_load_authorized_chunks`
- `retrieval/module2_knowledge_search.py::step2_search_milvus_hybrid`
- `retrieval/module2_knowledge_search.py::rerank_candidates`
- `retrieval/module2_knowledge_search.py::search_knowledge`

### 业务例子

> 企业知识库已经存有北京、上海、深圳等城市共 100 万个分块。用户询问：“深圳宝安中心附近适合亲子的景点有哪些？”

**现在的结果：** 系统先从 PostgreSQL 读取该用户有权访问的大量分块，再组织 ID 交给 Milvus 过滤。数据越多，读取越慢、占用内存越大，还可能召回文字相似的其他城市资料。

**改造后的结果：** 查询一开始就带上“租户、深圳、景点、亲子、有效版本”等条件。Milvus 先筛选再返回少量候选，PostgreSQL 只查询这些候选的原文。回答速度不会随着全部知识量线性变慢。

### 修改前

```text
查询
 -> PostgreSQL 读取用户有权查看的全部 Chunk
 -> Milvus 混合检索
 -> 用全部 Chunk ID 过滤结果
 -> 外部重排
 -> 返回证据
```

**主要问题：** 知识库越大，每次请求读取的数据越多，数据库、内存和 Milvus 过滤表达式都会变重。

### 修改后

```text
查询 + tenant_id + knowledge_base_id + city + category + valid_until
 -> Milvus 直接完成权限、激活版本和元数据过滤
 -> 取 top candidates
 -> PostgreSQL 只回表这些 candidate IDs
 -> 再次校验权限和有效期
 -> 重排；重排失败则回退 RRF 顺序
 -> 低于相关度阈值时追问或明确未找到
```

**改造作用：** 知识库从几千分块增长到大量分块后，检索仍然稳定；同时减少无证据回答。

**质量验证：** 为交通、景点、线路、美食、酒店建立固定问题集，统计 Recall@K、MRR 和引用正确率。

---

# ⑦ 天气、酒店和交通工具

**业务位置**

```text
Agent ->【实时旅游工具】-> 天气 / 酒店 / 交通 / 地图
```

**代码位置**

- `retrieval/module4_travel_tools.py::execute_travel_tool`
- `retrieval/module4_travel_tools.py::step2_call_amap`
- `retrieval/module4_travel_tools.py::step3_resolve_route_location`

### 业务例子

> 用户问：“10 月 2 日到 5 日去深圳，宝安中心附近 400 元以内、含早餐、可以免费取消的酒店有哪些？”

**现在的结果：** 高德只能返回附近有哪些酒店以及地址、评分等 POI 信息，无法证明 10 月 2 日是否有房，也没有实时价格和取消政策。

**改造后的结果：** 酒店供应商根据入住日期、人数和预算返回房型、含税价格、库存、早餐和取消规则。Agent 可以给出三家符合条件的酒店，并标注查询时间和预订链接。

### 修改前

```text
天气：高德天气，可以查询
酒店：高德 POI，只有酒店地点，没有房价和库存
交通：步行路线可用，跨城交通和票务不可用
工具结果：供应商原始字段直接交给 Agent
```

### 修改后

```text
天气供应商 -> WeatherResult
酒店供应商 -> HotelResult
交通供应商 -> TransportResult
高德地图   -> RouteResult

所有结果统一包含：
provider / queried_at / valid_until / source_url / timezone / currency

外部调用统一具备：
超时 / 缓存 / 限流 / 熔断 / 错误码 / 备用供应商
```

**改造作用：** 酒店可以回答指定日期是否有房、房型、含税价和取消规则；交通可以回答跨城车次或航班；替换供应商时不需要修改 Agent 主流程。

---

# ⑧ 大模型、嵌入和重排

**业务位置**

```text
Agent ->【模型服务】-> 意图、计划、回答、向量、重排
```

**代码位置**

- `retrieval/module1_model_gateway.py`
- `retrieval/module1_local_embedding.py`
- `retrieval/module2_knowledge_search.py::create_embeddings`
- `retrieval/module2_knowledge_search.py::rerank_candidates`

### 业务例子

> 用户询问深圳美食时，向量检索已经找到了 20 条候选资料，但外部重排服务突然超时。

**现在的结果：** 重排异常可能使整次对话失败，用户只看到“处理失败”，即使系统已经找到了可以使用的资料。

**改造后的结果：** 系统记录“重排服务超时”，自动使用 RRF 原始排序继续回答，并在监控中产生告警。用户仍然能获得结果，管理员可以单独处理重排服务问题。

### 修改前

```text
每次模型调用创建 HTTP 客户端
本地 BGE-M3 加载在 worker 进程内
重排服务失败可能影响整个回答
没有完整的 token、费用和模型阶段统计
```

### 修改后

```text
统一 ModelGateway
   ├-> 复用 HTTP 连接池
   ├-> 分阶段超时
   ├-> 重试和熔断
   ├-> 主模型与备用模型
   └-> token、费用、时延和错误统计

BGE-M3 -> 独立 GPU worker 或嵌入服务 -> 批量嵌入
重排失败 -> 记录告警 -> 使用 RRF 结果继续回答
```

**改造作用：** 减少连接耗时和 GPU 重复占用；模型或重排服务异常时不会拖垮全部对话；可以看清模型成本。

---

# ⑨ SSE 与前端

**业务位置**

```text
后台执行 ->【消息流】-> 用户聊天页面
```

**代码位置**

- `retrieval/module6_conversation_api.py::stream_run`
- `retrieval/module6_conversation_api.py::cancel_run`
- `frontend/src/api.ts`
- `frontend/src/views/ChatView.vue`
- `frontend/src/views/AdminView.vue`

### 业务例子

> 用户通过局域网电脑询问行程，回答生成到一半时 Wi-Fi 断开 5 秒，然后重新连接。

**现在的结果：** 页面可能停止更新，需要重新打开会话；系统也不清楚浏览器已经接收到了哪一段内容。

**改造后的结果：** 每段回答都有事件 ID。浏览器重连时提交最后收到的 ID，服务端只补发后面的内容，不会丢失或重复显示。用户点击停止时，取消信号也会继续传到正在执行的模型请求。

### 修改前

```text
浏览器 SSE 连接
 -> 后端每约 350ms 查询数据库
 -> 返回新增内容

断网后没有完整断点续传
停止生成主要更新数据库状态
前端接口类型存在大量 any
```

### 修改后

```text
worker -> Redis Stream -> SSE 网关 -> 浏览器
                       ├-> 事件递增 ID
                       ├-> Last-Event-ID 断点续传
                       ├-> 心跳
                       └-> 自动重连和事件去重

停止生成 -> 取消令牌 -> Agent -> 模型和工具请求立即终止
OpenAPI -> 自动生成 TypeScript 类型和 API 客户端
```

**改造作用：** 多用户聊天时不再高频轮询数据库；网络闪断后继续显示回答；停止按钮真正停止费用消耗。

---

# ⑩ 数据库迁移

**业务位置**

```text
用户、文档、消息、计划、任务 ->【数据库结构】-> PostgreSQL
```

**代码位置**

- `backend/trippilot/models.py`
- `migrations/versions/0001_initial.py`

### 业务例子

> 新版本需要给任务表增加 `dead_lettered_at` 字段。测试服务器是新数据库，启动正常；生产服务器已经有半年数据。

**现在的结果：** `create_all()` 只擅长创建不存在的表，不会可靠地把已有表升级成新结构。应用上线后访问新字段时可能直接报错。

**改造后的结果：** 发布时先执行 `0002_work_lease.py`，明确增加字段、索引和默认值。迁移失败则停止发布；需要回滚时执行对应的 downgrade，不破坏其他数据。

### 修改前

```python
def upgrade():
    Base.metadata.create_all(op.get_bind())

def downgrade():
    Base.metadata.drop_all(op.get_bind())
```

**主要问题：** 这不是可审计的结构变化记录，生产环境无法安全判断某次版本究竟改了哪些表和字段。

### 修改后

```text
0001_initial.py       明确创建当前所有表、索引、外键和约束
0002_work_lease.py    增加任务心跳和死信字段
0003_auth_tokens.py   增加 refresh token 和账号状态
0004_memory_schema.py 增加记忆来源和 schema_version

每个迁移：可升级、可回滚、可在测试数据库验证
```

**改造作用：** 数据库升级过程可检查、可回滚，不会因为一次部署损坏已有用户数据。

**同时增加：** `updated_at`、操作者、必要的 `deleted_at`、状态约束、明确的外键删除策略和 JSON `schema_version`。

---

# ⑪ 监控与问题定位

**业务位置**

```text
接口 -> 队列 -> 检索 -> 工具 -> 模型 -> 回答
  └──────────【同一个 trace_id】──────────┘
```

**代码位置**

- `retrieval/module7_performance_monitor.py`
- `backend/trippilot/app.py`
- worker 日志与部署配置

### 业务例子

> 用户反馈：“为什么今天问一个深圳三日游等了 80 秒？”

**现在的结果：** 管理员只能查看零散日志，难以判断是排队、BGE-M3、Milvus、重排、高德还是大模型慢。

**改造后的结果：** 根据本次请求的 trace ID 可以直接看到：排队 52 秒、检索 0.8 秒、高德 0.6 秒、大模型首 token 18 秒、生成 8 秒。管理员立即知道主要问题是聊天 worker 不足，而不是检索或地图接口。

### 修改前

```text
部分步骤耗时写入业务数据库
日志缺少完整跨组件链路
没有统一指标、仪表盘和告警
```

### 修改后

```text
OpenTelemetry：串联 API、Celery、检索、模型和外部工具
Prometheus：记录 P50/P95/P99、首 token、队列等待和错误率
JSON 日志：trace_id、run_id、job_id、stage、error_code
告警：队列积压、模型故障、供应商故障、GPU、磁盘和数据库
```

**改造作用：** 助手回答慢时，可以直接判断时间花在排队、检索、重排、工具还是模型，不再靠猜测。

---

# ⑫ 测试、Git 与部署

**业务位置**

```text
代码修改 ->【测试和 CI】-> 镜像 ->【部署】-> 生产环境
```

**代码位置**

- `tests/conftest.py`
- `tests/`
- `frontend/`
- `deploy/compose.infrastructure.yml`
- 待新增 `.github/workflows/` 或企业 CI 配置
- 待新增应用 Dockerfile

### 业务例子

> 开发人员修改了文档上传功能，在自己电脑上测试正常，但测试代码继承了开发 `.env`。另一台电脑没有启动 MinIO，4 个测试直接失败；如果跳过测试上线，还可能把问题带到生产。

**现在的结果：** 测试结果依赖开发机环境；仓库还没有首个 Git 提交，也没有自动流水线阻止失败版本发布。

**改造后的结果：** 单元测试强制使用临时本地存储，集成测试自行启动 MinIO 等依赖。代码只有在后端测试、前端测试、数据库迁移检查和安全扫描全部通过后才能构建镜像并灰度发布。

### 修改前

```text
后端测试：24 通过，4 失败
失败原因：测试读取开发 .env，错误连接本机 MinIO

前端：只有类型检查和构建，没有组件和 E2E 测试
Git：仓库没有首个提交
部署：Compose 只管理基础设施，应用进程依靠手工脚本
备份：没有经过验证的恢复演练
```

### 修改后

```text
单元测试：强制 fake/local 配置，不读取开发 .env
集成测试：Testcontainers 启动 PostgreSQL、Redis、MinIO、Milvus
前端测试：Vitest + Playwright
Agent 评测：固定意图、证据、计划约束和拒答测试集

Git 提交 -> lint -> 单测 -> 前端测试 -> 构建
         -> 迁移检查 -> 集成测试 -> 安全扫描
         -> 构建镜像 -> 测试环境 -> 灰度发布 -> 监控 -> 回滚
```

**改造作用：** 测试结果不再受开发机影响；每次修改可以审计和回滚；服务器重启后应用自动恢复；发布失败不会影响全部用户。

---

## 2. 最终改造效果对照

### 修改前

```text
能运行的完整原型
├── 文档可以上传和检索
├── Agent 可以规划和回答
├── 天气和地图可用
├── 具备基础记忆
└── 适合本地演示和小规模使用
```

### 修改后

```text
可上线的企业系统
├── 账号、权限、密钥和审计可控
├── 聊天和文档处理互不阻塞
├── 大知识库下检索仍然稳定
├── 记忆可追溯、可更正、可删除
├── 酒店、交通和天气有统一实时数据接口
├── 故障可以自动重试、降级和告警
├── 数据库和发布可以升级、回滚和恢复
└── 每次代码、模型和 Prompt 变化都有测试依据
```

## 3. 实施原则

当前不需要立刻拆成大量微服务。继续保留 `ingestion` 和 `retrieval` 两个主要业务包，先完成：

```text
测试隔离
  -> 队列可靠性
  -> 检索扩展性
  -> 数据库迁移
  -> 认证安全
  -> 监控部署
```

只有 BGE-M3、文档处理或 SSE 需要独立扩容时，再把对应部分拆成服务。
