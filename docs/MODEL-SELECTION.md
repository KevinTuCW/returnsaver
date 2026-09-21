# 模型选型实测记录

端点：`https://api.z.ai/api/paas/v4/`（智谱 GLM 海外端点，OpenAI 兼容）
测试日期：2026-09-21 · 所有数字为本机实测，非厂商标称值

## 一、可用模型

`GET /models` 返回 11 个：

```
glm-4.5   glm-4.5-air   glm-4.6   glm-4.7
glm-5     glm-5-turbo   glm-5.1   glm-5.2
glm-5.3   glm-5.3-flash glm-5.3-flashx
```

**旧文档里的 `glm-4-flash` / `glm-4-air` 在这个端点不存在**（报错 1211 Unknown Model）。照着国内端点的模型名写会直接挂——这类命名差异是接第三方 LLM 时最容易踩的坑。

## 二、能力矩阵（实测）

| 模型 | JSON mode | Function calling | 可关思考链 | 分类 out-tokens |
|---|---|---|---|---|
| `glm-5.3-flashx` | ✓（需 max_tokens≥800） | — | **✗ 报错 1210** | 152 |
| `glm-5.3-flash` | ✓（需 max_tokens≥800） | — | **✗ 报错 1210** | 126 |
| `glm-5.3` | — | **✗ 不返回 tool_calls** | — | — |
| `glm-4.5-air` | ✓ | ✓（**关思考链后失效**） | ✓ | **41** |
| `glm-5-turbo` | ✓ | ✓ | ✓ | 41 |
| `glm-4.5` | ✓ | ✓ | ✓ | 41 |
| `glm-4.6` | ✓ | ✓ | ✓ | 41 |

## 三、三个反直觉的发现

### 1. 「flash」档比「air」档贵

`glm-5.3-flash*` **强制思考、关不掉**（错误码 1210：`This model always engages in thinking`）。一个「把用户这句话分成 6 类」的任务，它要烧 126–152 个输出 token 想清楚；`glm-4.5-air` 关掉思考链后只要 **41 个**，答案完全一致。

名字里带 flash 不等于便宜。**选意图模型要看能不能关推理，不是看名字。**

### 2. 推理模型会把 max_tokens 全烧在思考上，content 返回空串

第一次接的时候全线失败，报 `Expecting value: line 1 column 1`，看起来像 JSON mode 不支持。实际是：

```
finish_reason : length
content       : ''
reasoning_content : 'The user says "I want to return" - this appears to be...'
```

`max_tokens=200` 被 reasoning 吃光了，正文一个字没写。两个解法：关思考链（首选），或把 max_tokens 提到 800+（治标，还是在烧钱）。

代码里两个防御都做了：`MODEL_MAX_TOKENS` 留足预算，`content` 为空时抛 `LLMUnavailable` 降级到规则快路，而不是让用户看到空白回复。

### 3. 思考链对挽留话术是纯负收益

同一个 prompt，开/关思考链对比：

| 模型 | 思考链 | 延迟 | out-tokens | 话术开头 |
|---|---|---|---|---|
| `glm-4.6` | 开 | **19.3s** | 214 | "Sarah, sorry to hear the Merino Crew Tee didn't…" |
| `glm-4.6` | 关 | **10.3s** | 76 | "Sarah, we're sorry the Merino Crew Tee didn't…" |
| `glm-4.5` | 开 | 21.9s | 249 | "Sarah, I'm sorry the Merino Crew Tee didn't fi…" |
| `glm-4.5` | 关 | 9.4s | 59 | "Sarah, we're sorry the Merino Crew Tee didn't…" |
| `glm-5-turbo` | 开 | 15.7s | 174 | 同上 |
| `glm-5-turbo` | 关 | 9.0s | 79 | 同上 |

**延迟翻倍、token 涨 3 倍，话术几乎一模一样。** 挽留话术就是「共情一句 + 给个方案」两三句话，不是需要推理的任务。三档一律关思考链。

在聊天窗口这个场景里，**多等 10 秒比话术差一点严重得多**——用户直接关窗，连挽留的机会都没有。

## 四、最终选型

| 档位 | 模型 | 思考链 | max_tokens | 实测延迟 | 用途 |
|---|---|---|---|---|---|
| **意图** | `glm-4.5-air` | 关 | 256 | <1s | 分类 + 情绪分，规则快路未命中时才调 |
| **生成·小** | `glm-5-turbo` | 关 | 512 | ~8.9s | 常规挽留话术。`glm-4.5-air` 关思考链后不返回 tool_calls，所以生成侧不能用它 |
| **生成·大** | `glm-4.6` | 关 | 1024 | ~13.5s | 高情绪 / 高客单 / 二轮僵持 / VIP |

全部可经 `.env` 覆盖：`RS_MODEL_*` / `RS_THINKING_*` / `RS_MAX_TOKENS_*`。

## 五、实测成本（6 个场景走完整流程）

```
llm_cost_usd_total            : 0.006843
avg_cost_per_conversation_usd : 0.001141
vs case 预算 $0.15            : 0.8%
routing                       : none 13 / large 2 / small 1
```

13 次零调用（规则快路 + 确定性模板）是成本压到预算 1% 以内的主因，不是选了便宜模型。

> `MODEL_PRICING` 仍是量级占位值，上线前必须按合同价替换。成本的**相对结构**（零调用占比、三档分布）是实测的，绝对值不是。

## 六、待办

- **`glm-4.6` 13.5s 仍超出 12s 延迟预算**，已打 `LATENCY_BUDGET_EXCEEDED` 告警。下一步应上**流式输出**：首字延迟能压到 1–2s，用户感知的等待基本消失。这是 MVP 之后的第一优先级。
- 意图侧可再做一层语义缓存（同义问法命中率高），把小模型调用再削掉一部分。
