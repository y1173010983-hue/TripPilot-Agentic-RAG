# TripPilot 语义路由模型微调方案 V1

## 1. 训练目标

本轮不再训练模型直接生成工具调用，而是让模型把不同自然语言表达统一转换为稳定的业务语义：

```json
{
  "operation": "book",
  "domains": ["hotel"],
  "entities": {
    "hotel_area": "宝安中心"
  },
  "state_changes": [],
  "clarification": null
}
```

后端根据 `operation + domains + entities` 选择工具，根据 `state_changes` 更新长期旅行状态。

```mermaid
flowchart LR
    A["用户自然语言"] --> B["Qwen3-4B Semantic Router"]
    B --> C["标准语义 JSON"]
    C --> D["后端状态处理器"]
    C --> E["后端工具路由器"]
    D --> F["旅行需求记忆"]
    E --> G["天气、酒店、景点、路线等工具"]
```

## 2. 为什么重新从基座训练

V5 LoRA 已经学习了旧协议 `intent/domains/search_query/requirement_updates/tools`。新协议字段和任务边界均已改变，因此本轮从 Qwen3-4B 基座创建新的 LoRA，不继续叠加 V5，避免旧字段干扰。

## 3. 数据集设计

| 数据集 | 数量 | 用途 |
|---|---:|---|
| Train | 453 | LoRA 参数训练 |
| Dev | 151 | 训练过程评估和过拟合观察 |
| Test | 151 | 训练结束后的独立验收 |

三套数据的用户输入完全无重合。每条输入都包含：

- `current_date`：用于相对日期解析；
- `current_state`：当前保存的旅游需求；
- `user_input`：用户本轮原始表达。

数据覆盖：

- 酒店区域修改、保持不变、替换酒店但不换区域；
- 酒店和景点预订；
- 酒店和景点比较；
- 饮食、人数、房间数、节奏、兴趣等状态变化；
- 仅保存偏好、暂不规划；
- 天气、交通、美食、景点查询；
- 多条件同时修改；
- 同一句中反复修改并以最后值为准；
- 缺少具体指代时发起澄清；
- 问候、格式整理和非旅游问题。

## 4. 训练参数

| 参数 | 值 |
|---|---|
| 基座模型 | Qwen3-4B-Instruct-2507 |
| 训练方式 | 4-bit QLoRA |
| LoRA rank / alpha | 16 / 32 |
| 学习率 | 3e-5 |
| Epoch | 3 |
| 有效 batch size | 16 |
| 最大长度 | 1024 |
| 精度 | BF16 |

输出目录：

```text
/root/autodl-tmp/trippilot-finetune/outputs/qwen3-4b-semantic-router-v1
```

## 5. 验收指标

验收脚本分别计算：

- JSON 协议合法率；
- `operation` 准确率；
- `domains` 准确率；
- `entities` 必需字段准确率；
- `state_changes` 完全匹配率；
- 澄清行为准确率；
- 严格整条通过率。

建议上线候选标准：JSON 合法率不低于 99%，operation 准确率不低于 90%，state_changes 准确率不低于 90%，严格通过率不低于 85%。

## 6. 一键训练

在 AutoDL 终端执行：

```bash
cd /root/autodl-tmp/trippilot-finetune && bash scripts/train_semantic_router_v1_one_click.sh
```

脚本自动完成依赖检查、GPU 检查、数据生成、JSON 校验、训练、独立测试和报告生成。

最终验收报告：

```text
/root/autodl-tmp/trippilot-finetune/outputs/qwen3-4b-semantic-router-v1/semantic_acceptance.json
```
