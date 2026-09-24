<div align="center">

# 🛟 Return Saver

**退货挽留 AI Agent** —— 五阶段状态机 · 五场景策略引擎 · 两级意图 + 三档生成路由 · 四层护栏 + 体验不变量断言 · Langfuse 全链路 + 四类分数 · 双口径挽留率 + 强制 holdout

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Pydantic](https://img.shields.io/badge/Pydantic-v2-e92063.svg)](https://docs.pydantic.dev/)
[![Langfuse](https://img.shields.io/badge/Langfuse-v4%20tracing-fbbf24.svg)](#-可观测)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-optional-336791.svg?logo=postgresql&logoColor=white)](#postgresql)
<<<<<<< HEAD
[![tests](https://img.shields.io/badge/e2e%20tests-36%2F36-brightgreen.svg)](#-验证)
=======
[![tests](https://img.shields.io/badge/tests-168%20passed-brightgreen.svg)](#-验证)
>>>>>>> feat/multitenant-config
[![guardrails](https://img.shields.io/badge/护栏-4%20层%20L1→L4-brightgreen.svg)](#️-四层护栏)
[![experience violations](https://img.shields.io/badge/体验不变量违反-0-brightgreen.svg)](#-体验不变量)
[![cost](https://img.shields.io/badge/单次会话成本-0.7%25%20of%20budget-brightgreen.svg)](#-经济效率两级意图--三档生成路由)

面向跨境 DTC Shopify 品牌 · chat widget 检测到退货意图后调用本服务

</div>

---

Return Saver 在**不损伤用户体验**的前提下尽量把一单退货救回来。它不是一个 prompt 加一个聊天框——退货流程被拆成五个可单测的阶段，退货原因被分成五个场景各配一条确定性策略，**金额与动作只从策略引擎出**，模型只负责措辞。默认零配置即可完整演示：没有 key 时 LLM 降级到确定性 mock、Langfuse 变 no-op、存储走内存，断网限流都不翻车。

**两条不可违背的主张**

1. **LLM 有建议权，没有执行权** —— 金额与动作只从确定性策略引擎出，四层护栏兜底。模型的输出结构里**根本没有金额字段**。
2. **降退货率不得以伤体验换** —— 体验约束写成会抛错的断言（`guardrails.assert_experience_invariants`），不是 prompt 里的建议。

一次真实链路跑通的样子（`scripts/verify_live.py`，真 GLM + 真 Langfuse，6 场景走完整流程，单次运行原样节选）：

```
✓ 价值差·VIP·$100 → 大模型        offer_made / value_gap / offer_compensation    10.0s
    路由 large/glm-4.6 (vip_customer)                   tok=448/68  $0.003096
✓ 使用问题·$260 → 大模型 + 手册    offer_made / usage_issue / send_guide           9.7s
    路由 large/glm-4.6 (high_value_order_worth_the_spend) tok=459/81  $0.003240
✓ 破损·$38 → 小模型 + 修换         offer_made / product_damage / repair_exchange   9.5s
    路由 small/glm-5-turbo (default_small_model)        tok=406/50  $0.000274
✓ 超窗口 → 模板婉拒（零调用）       declined / not_eligible / decline_with_rules    0.0s
✓ 情绪+小额 → 秒退（零调用）        instant_refund / emotional_insist              0.0s
✓ 情绪+大额 → 人工 P1（零调用）     escalated / emotional_insist · 工单 + 2h SLA    0.0s
──────────────────────────────────────────────────────────────────────────────
✓ 护栏 bad_offer  → HTTP 422 OFFER_NOT_IN_ALLOWLIST
✓ 护栏 bad_text   → HTTP 422 FORBIDDEN_PHRASE
✓ 护栏 bad_amount → HTTP 422 UNAPPROVED_AMOUNT
✓ CSAT 上报 langfuse=True
  avg_cost_per_conversation_usd 0.001102 · vs case 预算 $0.15 = 0.7%
  routing none 13 / large 2 / small 1
✓ 全部通过
```

<sub>13 次零调用（规则快路 + 确定性模板）才是成本压到预算 1% 以内的主因，不是选了便宜模型。token 数与延迟每次运行有波动（temperature 0.4），路由档位与 <code>route_reason</code> 是确定性的。</sub>

## 📑 目录

- [✨ 特性](#-特性)
- [🏗️ 架构](#️-架构)
- [🧱 技术栈](#-技术栈)
- [🚀 快速开始](#-快速开始)
- [🔌 接口](#-接口)
- [💬 使用示例](#-使用示例)
- [🔄 退货流程五阶段状态机](#-退货流程五阶段状态机)
- [🎯 五个场景与对应策略](#-五个场景与对应策略)
- [💰 经济效率两级意图 + 三档生成路由](#-经济效率两级意图--三档生成路由)
- [🛡️ 四层护栏](#️-四层护栏)
- [🤝 体验不变量](#-体验不变量)
- [📐 指标口径](#-指标口径)
- [📊 验证](#-验证)
- [🔒 安全](#-安全)
- [🔭 可观测](#-可观测)
- [📁 项目结构](#-项目结构)
- [🧩 配置](#-配置)
- [🗺️ 路线图](#️-路线图)
- [⚠️ 已知边界](#️-已知边界)
- [📄 许可](#-许可)

## ✨ 特性

- 🔻 **五阶段状态机** —— 意图识别 → 身份订单校验 → 确认订单 → 售后规则核验 → 场景执行，每阶段只做一件事、可单测。**S3 不确认绝不进入执行**——认错单就是动错钱。
- 🎯 **五场景确定性策略引擎** —— 不合规婉拒（附规则原文）/ 使用问题（手册视频不发钱）/ 价值不符（换码优先，其次阶梯 credit）/ 产品损坏（修换优先）/ 情绪激烈（分级秒退或人工）。每条策略都是代码，不是 prompt。
- 🚫 **LLM 只有建议权** —— 模型唯一出口是 `propose_copy(offer_id ∈ 白名单, message)`，**结构里刻意不设 amount 字段**，「退你 200%」在语法上不可表达。
- 🛡️ **四层护栏 L1→L4** —— 收窄动作空间 → Schema + 白名单 + ≤30% 上限 → 出参文本扫金额与越权承诺 → 执行层只认 HMAC `offer_token` + 幂等 + 独立复核。**哪怕前三层全崩，L4 也让模型的任何一句话动不了一分钱。**
- 🤝 **体验不变量是断言不是建议** —— 每一轮必须带放弃挽留的出口、婉拒必须引规则原文、情绪超阈值禁止任何 offer、最多 2 轮 8 次交互；违反即抛错 + 打点 + 上报 Langfuse 分数。
- 🔥 **情绪优先于合规** —— 情绪阈值判定**排在合规性之前**：已经发火的用户哪怕订单不合规，也不该再被推挽留方案。但不合规不会被自动秒退，而是记成 anomaly 强制转人工，并把违反的规则一起带给人工。
- 💰 **两级意图 + 三档生成路由** —— 意图侧永不调用大模型（规则快路命中即零调用）；生成侧按情绪 / 客单 / 轮次 / VIP 动态选 `none`（模板，$0）/ `small` / `large`。实测均成本 **$0.0011 / 次会话 = case 预算的 0.7%**。
- 🔌 **零配置即可完整演示** —— 没 key 时 LLM 自动降级到确定性 mock、Langfuse 变 no-op、存储走内存；模型抖动、空 content、`1210` 强制思考报错全部有降级路径，绝不让用户卡住。
- 📐 **双口径挽留率** —— `/api/metrics` 同时上报 `addressable_deflection_rate`（主口径，分母已排除破损/错发/超窗口）与 `overall_deflection_rate`（全量），防止销售话术和 QBR 打架。分母是**会话**不是轮次。
- 🧪 **强制 holdout 对照组** —— `RS_HOLDOUT_PCT=10` 做增量归因，60 天结算窗口。分桶走 `session_id` 的 SHA-256 摘要，不用内建 `hash()`——后者每进程带随机种子，换 worker 就换实验臂，归因直接作废。
- 🔭 **Langfuse 全链路 + 四类分数** —— 一次 `/api/negotiate` 一个 span，`session_id` 串起整段对话；`user-csat` / `guardrail-trip` / `experience-violation` / `retention-outcome` 四个分数按**信号来源**命名。
- 💾 **只持久化丢了会出事的三张表** —— 会话（重启不用重新确认订单）/ 执行幂等（**防重复发钱**，重启后仍幂等）/ 人工工单（带 `due_at`，直接算 `sla_breached`）。库连不上时写操作静默降级，主流程不受影响。

## 🏗️ 架构

```text
用户消息（chat widget 检测到退货意图后调用）
   │
   ▼
体验出口（want_return_anyway=true → 立刻 released，不多问一句）
   │
   ▼
额度护栏（round ≥ 2 → released · turns > 8 → released）
   │
   ▼
S1 意图识别 ─┬─ 规则快路命中（关键词 + 情绪词典）──▶ 零模型调用
   │         └─ 未命中 ──▶ 小模型 JSON 分类（永不用大模型）
   │  情绪只升不降；规则识别的强情绪压过模型判断
   ├─ intent=other ──▶ passthrough，交还主客服
   ▼
S2 身份与订单校验（查不到就要订单号 / 下单邮箱，不猜）
   │
   ▼
S3 确认订单（列近 60 天已签收候选，等 confirm_order_id；归属对不上 → order_mismatch）
   │  确认不占用谈判额度
   ▼
holdout 分桶（SHA-256(session_id) % 100 < RS_HOLDOUT_PCT → 纯对照，不挽留）
   │
   ▼
S4 售后规则核验（R-WINDOW / R-FINAL / R-HYGIENE / R-USED；破损与质量走豁免通道）
   │
   ▼
S5 场景分类 ─┬─ 情绪 ≥0.70 ──▶ EMOTIONAL_INSIST ─▶ ≤$50 且无异常 → 立即退款 + 回头钩子
   │         │        （最高优先级）              └▶ 否则 → 人工 P1/P2 + 2h SLA + 冻结窗口
   │         │                                      禁止任何挽留话术 allow_retention=False
   │         ├─ 不合规 ────▶ NOT_ELIGIBLE ──▶ 模板婉拒 + 规则原文 + 替代方案（零 LLM 成本）
   │         ├─ 破损/质量 ─▶ PRODUCT_DAMAGE
   │         ├─ 不会用 ────▶ USAGE_ISSUE
   │         └─ 尺码/价格/不想要 ─▶ VALUE_GAP（冷静期用户直接放行标准退货）
   ▼
策略引擎 build_resolution ──▶ offers 白名单（金额只从这里出，≤ 订单 30%）
   │
   ▼
生成路由 route_generation ──▶ none（模板 $0）/ small / large    会话预算耗尽 → 强制 none
   │
   ▼
L1 收窄动作空间：function call propose_copy(offer_id ∈ 白名单, message)   ← 结构里没有金额字段
   │
   ▼
L2 Schema(extra=forbid) + 白名单断言 + value ≤ 订单 30% ──失败──▶ 422 guardrail_blocked
   │
   ▼
L3 出参文本扫描（金额 / 百分比 / 越权承诺）─────────────失败──▶ 422 guardrail_blocked
   │
   ▼
体验不变量自检（出口 / 婉拒引规则 / 发火不挽留）──违反──▶ 打点 + Langfuse 分数 + 回复带 warning
   │
   ▼
offer + offer_token（HMAC-SHA256，TTL 15min，含 jti）
   │
   ▼
POST /api/accept ─▶ L4 幂等（内存 + 库双查）→ 验签 → 过期检查 → 独立复核上限 ─▶ executed
                        └─ 伪造 / 过期 / 超上限 ──▶ 422 BAD_SIGNATURE / TOKEN_EXPIRED / VALUE_OVER_CAP

（全程 Langfuse：一次 negotiate = 一个 span，session_id 串起整段对话，四类分数随事件上报）
```

- **策略引擎是唯一的金额来源**（`policy.build_resolution`）—— 返回 `{action, offers[], payload, allow_retention}`。`offers` 是 LLM 唯一能引用的动作集合，每个 offer 的 `value` 在生成前就已经被 `min(…, 订单 × 30%)` 夹过一次，L2 再校一次，L4 还要独立算第三次。
- **情绪判定优先于合规判定**（`policy.classify_scenario`）—— 这一条必须排在 `eligible` 之前，否则「不合规 + 已发火」会落进 `NOT_ELIGIBLE`，`allow_retention` 保持 `True`，等于对着一个发火的用户继续推挽留方案。
- **确认订单与谈判额度解耦**（`app._negotiate`）—— 先按「假如这是下一轮」算方案，确认真要挽留了才把 `round` 记上去；让利阶梯按 `round` 加码，所以第一次给方案不会被误算成第二轮的 25%。

## 🧱 技术栈

| 关注点 | 选型 | 理由 |
| --- | --- | --- |
| AI coding | Cursor + Claude Opus 5 | — |
| API / 编排 | **FastAPI** + Pydantic v2 | **护栏即 Schema**：`extra=forbid` + 字段约束就是 L1/L2 的实现 |
| LLM | **GLM 三档**（`glm-4.5-air` 意图 / `glm-5-turbo` 常规 / `glm-4.6` 高价值），OpenAI 兼容端点 | 换厂商只改 `.env` 三行，代码一行不动；模型名与能力是**实测**选定，见 [`docs/MODEL-SELECTION.md`](docs/MODEL-SELECTION.md) |
| 生成契约 | OpenAI **function calling**（`tool_choice` 强制） | 让模型只能产出 `{offer_id, message}`，自由文本无处可去 |
| 可观测 | **Langfuse v4**（`langfuse.openai` drop-in + scores） | 换个 import 就有 prompt/token/成本/延迟全链路 |
| 执行层信任 | **HMAC-SHA256** 签名 token（TTL 15min + `jti`） | 动钱这一步不信任上游任何输入，只信自己的签名 |
| 数据 | 内存（默认）/ **PostgreSQL 16**（可选，已实测落库） | 三张表 + 幂等唯一约束；memory 模式下整个 `db.py` 是 no-op |
| 测试 | pytest + `TestClient`，`conftest` 强制清空凭证 | 36 个 e2e 一秒跑完、不花钱、不因模型抖动假失败 |
| 部署 | Railway / Fly.io | — |

> 所有 LLM 调用走 OpenAI 兼容协议；不填 key 时全程确定性 mock，不触碰网络。

## 🚀 快速开始

**前置**：Python 3.12+。默认运行时**完全离线、确定性**，无需任何 key。

```bash
# 1. 建虚拟环境并安装
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # 填 key；不填也能跑

# 2. 跑测试（不需要起服务，强制 mock、不走网络）
.venv/bin/python -m pytest tests -q          # → 36 passed

# 3. 真实链路验证（打真 LLM + 上报真 Langfuse，需要 key）
.venv/bin/python scripts/verify_live.py      # → 6 场景 + 3 护栏 + CSAT 全过
```

`demo.sh` 是纯 HTTP 客户端，**要先把服务起起来**（uvicorn 是前台阻塞的，所以后台起或另开一个终端）：

```bash
.venv/bin/python -m uvicorn app:app --port 8777 > /tmp/rs.log 2>&1 &
bash demo.sh                # 12 段全流程演示，末尾会读 /tmp/rs.log 的护栏日志
```

12 段依次是：五个场景（含情绪分级的秒退与人工两条分支）→ L2/L3 三种护栏拦截 → L4 签名执行 + 幂等重放 + 伪造 token → 体验出口 → 人工工单队列 → PM 看板指标。服务没起时 `demo.sh` 会说人话并退出，不会喷一屏 JSON traceback。

测试与真实链路验证**刻意分开**：`tests/conftest.py` 清空凭证强制走 mock，保证测试一秒跑完、不花钱、不因模型抖动假失败；真实链路由 `verify_live.py` 单独验。

> 本机若开着代理，`curl` 需加 `--noproxy '*'`，否则 localhost 会被代理劫持。

## 🔌 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/negotiate` | 主入口：跑完五阶段，返回话术 + offer + `offer_token`。一次调用 = 一个 Langfuse span |
| `POST` | `/api/accept` | **L4 执行层**：只认签名 `offer_token` + `idempotency_key`，重放返回 `already_executed` |
| `GET` | `/health` | 如实反映 LLM / Langfuse / 存储各项配置状态，**绝不回显任何密钥本身** |
| `GET` | `/api/metrics` | PM 看板：双口径挽留率 / 成本 / 路由分布 / 护栏触发 / holdout |
| `GET` | `/api/policy` | 商家售后规则原文（婉拒时引用的就是这份） |
| `GET` | `/api/manual-queue` | 人工工单队列；接了 Postgres 时直接返回 `sla_breached` |
| `POST` | `/api/csat?score=1..5&session_id=` | 会话结束评分，同时进本地看板与 Langfuse `user-csat` |
| `GET` | `/admin` | 商户侧 Shopify App 风格外壳：三项主菜单，供 C1/C2/C3 挂载 |


`/api/negotiate` 的关键入参：`session_id`（续会话）· `customer_id` · `message` · `confirm_order_id`（S3 确认）· `want_return_anyway`（体验出口，随时放弃挽留）· `force`（**仅演示护栏**：`bad_offer` / `bad_text` / `bad_amount`）。

`score` 的范围必须在接口层就框死——这是个无鉴权的写接口，放一个 `99` 进来就能把 `avg_csat` 这条对外指标彻底带偏。

配了 `RS_API_KEYS` 后 `/api/negotiate` 与 `/api/accept` 都要求 `X-API-Key`，**身份只来自凭证，永不来自请求体**。key 映射到 `"tenant"` 或 `"tenant:customer"`：

- **租户级 key**（`"public"`）—— 服务间调用用这种。客户主体由上游（helpmate）鉴权后透传在请求体的 `customer_id` 里。
- **客户级 key**（`"public:C-001"`）—— 只能代表那一个客户说话，请求体换成别的客户直接 **403**。

会话是租户级资源：拿 B 租户的 key 续 A 租户的会话也是 **403**。

## 💬 使用示例

**S3 先确认订单**，绝不跳过：

```bash
curl -s --noproxy '*' -X POST localhost:8777/api/negotiate \
  -H 'Content-Type: application/json' \
  -d '{"customer_id":"C-001","message":"It is too small, I want a return"}'
```

```jsonc
{
  "status": "awaiting_order_confirmation",
  "stage": "S3_confirm",
  "reply": "为了不弄错，先跟你确认一下是这单吗？",
  "candidates": [
    { "order_id": "ORD-1001", "product": "Merino Crew Tee (Black / M)",
      "total": 100.0, "delivered_days_ago": 6 }
  ],
  "offer": null,                       // 未确认订单前绝不给方案
  "next_action": "reply_with_confirm_order_id",
  "session_id": "S-db4b16a8bd43",
  "escape_hatch": "随时回「还是要退」，我立刻转标准退货，不会多问一句。",
  "cost_usd": 0.0,
  "model_calls": [ { "tier": "none", "model": "rule-fastpath", "cost_usd": 0.0 } ]
}
```

**带 `confirm_order_id` 推进到 S5**（真实 GLM 输出）：

```bash
curl -s --noproxy '*' -X POST localhost:8777/api/negotiate \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"S-db4b16a8bd43","customer_id":"C-001",
       "message":"It is too small, I want a return","confirm_order_id":"ORD-1001"}'
```

```jsonc
{
  "status": "offer_made", "scenario": "value_gap", "action": "offer_compensation",
  "stage": "S5_execute",
  "reply": "Hi Sarah, sorry the Merino Crew Tee didn't fit as expected! … the easiest fix is a free size exchange — we have S, L, and XL in stock right now, and we'll cover shipping both ways.",
  "offer": { "offer_id": "FREE_EXCHANGE", "type": "exchange", "value": 0.0,
             "label": "免费换码（现货 S/L/XL），双程运费我们出" },
  "offer_token": "7b2265787022…7d.8ea5fdbb9c49a87be1d93d315ee66e83",   // HMAC 签名，TTL 15min
  "alternatives": [
    { "offer_id": "CREDIT_15",         "label": "保留商品，赠 $15.00 店铺 credit" },
    { "offer_id": "REFUND_PARTIAL_15", "label": "保留商品，原路退 $15.00" }
  ],
  "next_action": "await_customer_decision",
  "cost_usd": 0.00324,
  "model_calls": [
    { "tier": "none",  "model": "rule-fastpath" },
    { "tier": "none",  "model": "rule-fastpath" },
    { "tier": "large", "model": "glm-4.6", "cost_usd": 0.00324,
      "tokens_in": 448, "tokens_out": 92, "latency_s": 11.69,
      "route_reason": "vip_customer" }                 // VIP + ≥$80 才升大模型
  ]
}
```

注意话术里**一个金额都没有**——金额由系统渲染成 offer 卡片，这是 L3 扫描强制的结果。

**护栏演示**（`force` 让模型故意越权，L3 当场拦下）：

```bash
curl -s --noproxy '*' -X POST localhost:8777/api/negotiate \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"S-…","customer_id":"C-001","message":"too small",
       "confirm_order_id":"ORD-1001","force":"bad_text"}'
```

```jsonc
// HTTP 422
{
  "status": "guardrail_blocked",
  "guardrail": { "layer": "L3", "code": "FORBIDDEN_PHRASE",
                 "detail": "文本包含越权承诺：'Keep the product'" },
  "reply": "抱歉，这条我没法处理，已经为你转到标准退货流程，不会耽误你。",
  "offer": null,
  "next_action": "fallback_to_standard_return",
  "escape_hatch": "随时回「还是要退」，我立刻转标准退货，不会多问一句。"
}
```

被护栏拦下也**必须留着体验出口**——拦截是我们的内部故障，不该变成用户的死胡同。

**L4 执行与幂等**：

```bash
# 正常执行
curl -s --noproxy '*' -X POST localhost:8777/api/accept \
  -H 'Content-Type: application/json' \
  -d '{"offer_token":"7b2265787022…","idempotency_key":"k-0001"}'
# → {"status":"executed","order_id":"ORD-1001","offer_id":"FREE_EXCHANGE","value":0.0,…}

# 同一个 key 重放 → 绝不二次发钱
# → {"status":"already_executed", …executed_at 与首次完全一致}
```

## 🔄 退货流程五阶段状态机

| 阶段 | 做什么 | 归属 | 关键设计 |
|---|---|---|---|
| **S1 意图识别** | 意图 + 退货原因 + 情绪分 | 规则快路 → **小模型** | 永不调用大模型；情绪超阈值由规则直接定，不交给模型 |
| **S2 信息校验** | 用户身份、订单归属、可退候选 | **[Code]** | 查不到就要身份信息，不猜 |
| **S3 确认订单** | 列候选，等用户显式确认 | **[Code]** | 不确认绝不进入执行——认错单就是动错钱 |
| **S4 规则核验** | 窗口 / final sale / 卫生 / 使用痕迹 | **[Code]** | 破损与质量走豁免通道；`R-USED` 只约束无理由退货 |
| **S5 场景执行** | 分类 → 选方案 → 生成话术 → 护栏 | **[Code]** 决策 / **[LLM]** 措辞 | 金额只从策略引擎出 |

确认订单**不占用**谈判额度；`round` 只统计真正发出过的挽留轮次。

`R-USED` 的规则原文是「已明显使用的不支持**无理由**退货」，所以代码里它只约束 `changed_mind`：使用类问题必须先用过才会发现，尺码试穿也不算明显使用，拿这条挡人会踩体验红线。

## 🎯 五个场景与对应策略

| 场景 | 触发 | 策略 | 是否花钱 |
|---|---|---|---|
| **不满足售后规则** | 超窗口 / final sale / 卫生类 | 附**规则原文**婉拒 + 必给替代方案（维修或小额券） | 极少 |
| **产品使用问题** | 不会用 / 连不上 / 预期错位 | 手册 + 视频 + 三条技巧 + 窗口延长 14 天 + 1v1 指导 | **否** |
| **价格价值不符** | 尺码不合 / 觉得不值 / 不想要 | 免费换码优先，其次阶梯 credit（15% → 25%） | 是，≤30% |
| **产品损坏** | 破损 / 错发 / 质量 | 免费维修 → 免费换新 → 带瑕疵保留补差 | 视情况 |
| **情绪激烈执意退** | 情绪分 ≥ 0.70 | **分级**：≤$50 且无异常 → 立即退；否则 → 人工介入 P1/P2 + 2h SLA | 退款 |

情绪激烈场景 **禁止任何挽留话术**（`allow_retention=False`，违反会被断言拦下）。立即退款附带 `comeback_credit` 回头钩子——把一次差体验变成下次再来的理由。

情绪阈值的判定**排在合规性之前**：一个已经发火的用户，哪怕订单不合规，也不该再被推挽留方案。这类会话不会被自动秒退——不合规会被记成 anomaly 强制转人工，并把违反的规则一起带给人工。价值补偿线另有冷静期：风险用户或 90 天内谈判 ≥3 次直接放行标准退货，不再给券。

> 冷静期给的是**空 offers**，所以必须在进护栏前就 `released` 返回。继续往下走会被 L2 白名单必然拦下，把一条正常业务路径变成 422 护栏拦截，还污染护栏指标。

## 💰 经济效率两级意图 + 三档生成路由

**意图侧**（永不用大模型）

1. 规则快路：关键词 + 情绪词典命中 → **零模型调用**（生产中约覆盖 60–70% 流量）
2. 未命中 → 小模型 JSON 分类。规则识别到的强情绪会压过模型输出（`emotion = max(模型, 规则)`）——宁可保守，也不能漏掉愤怒用户

**生成侧动态路由**

| 档位 | 触发条件 | 成本 |
|---|---|---|
| `none`（模板） | 婉拒场景（**不看情绪**）、情绪激烈场景、会话预算耗尽 | **$0** |
| `small` | 默认 | 低 |
| `large` | 情绪 ≥0.45 / 订单 ≥$200 / 第二轮僵持 / VIP 且 ≥$80 | 高但值得 |

婉拒档之所以**不看情绪**：只有模板分支会写 `status="declined"` + `cited_rules`，走模型就等于给出一条没有规则依据的婉拒，直接违反 `decline_must_cite_rule`。情绪真高到硬阈值时，场景已经被判成 `EMOTIONAL_INSIST`，根本走不到这里。

**真实 LLM 实测**（`scripts/verify_live.py`，6 个场景走完整流程）：

| 指标 | 值 |
|---|---|
| `llm_cost_usd_total` | **$0.006610** |
| `avg_cost_per_conversation_usd` | **$0.001102** |
| `vs_case_budget` ($0.15) | **0.7%** |
| `cost_per_deflection_usd` | $0.001653 |
| routing | none **13** / large 2 / small 1 |
| 延迟 | 模板 **0s** · `glm-5-turbo` ~9.5s · `glm-4.6` ~10s（多次运行落在 9.7–13.5s） |
| `experience_violations` | **0** |

13 次零调用（规则快路 + 确定性模板）才是成本压到预算 1% 以内的主因，不是选了便宜模型。单会话另有 `RS_COST_BUDGET` 硬预算，超了强制降档到模板。

三个反直觉的选型坑详见 [`docs/MODEL-SELECTION.md`](docs/MODEL-SELECTION.md)：

- 旧文档里的 `glm-4-flash` / `glm-4-air` 在 z.ai 端点**不存在**（报 1211 Unknown Model）
- **「flash」档比「air」档贵**：`glm-5.3-flash*` 强制思考关不掉（错误码 1210），分类任务烧 126–152 token，`glm-4.5-air` 关思考链只要 41
- **思考链对挽留话术是纯负收益**：延迟翻倍（19–22s → 9–10s）、输出 token 涨 3 倍，话术几乎一样

## 🛡️ 四层护栏

| 层 | 机制 | 位置 | 演示 |
|---|---|---|---|
| **L1** 收窄动作空间 | LLM 只能 function call `propose_copy(offer_id ∈ 白名单)`；**结构里没有金额字段**，「退 200%」在语法上不可表达 | `models.py` · `llm.TOOL` | — |
| **L2** Schema + 策略校验 | Pydantic `extra=forbid` + 白名单断言 + `value ≤ 订单 30%` | `guardrails.validate_proposal` / `validate_value_cap` | `force:"bad_offer"` → 422 `OFFER_NOT_IN_ALLOWLIST` |
| **L3** 出参文本扫描 | 正则扫金额/百分比/`keep the product`/`refund you`/`全额退款`，未批准一律拦 | `guardrails.scan_output_text` | `force:"bad_text"` → `FORBIDDEN_PHRASE`；`force:"bad_amount"` → `UNAPPROVED_AMOUNT` |
| **L4** 执行层隔离 | 动钱只认 HMAC `offer_token`（TTL 15min + `jti` + 幂等键**内存与库双查**，重启后仍幂等），并**独立复核**上限 | `guardrails.issue_offer_token` / `verify_offer_token` · `app.accept` | 伪造 token → `BAD_SIGNATURE`；重放 → `already_executed` |

**prompt 里写「不要承诺退款」不是护栏。** 哪怕前三层全崩，L4 也让模型的任何一句话动不了一分钱。

L4 的三个细节值得单说：

- **幂等必须查库**。内存幂等表活不过重启，光靠它会在重启后对同一个 key 二次执行 = 二次发钱。
- **`jti` 让每次签发都是唯一串**。载荷若只有订单 + 金额 + 过期时间（秒级），同一秒内对同一 offer 的两次签发会得出完全相同的 token，两次独立签发在事后无从区分。
- **独立复核不信任上游**。`verify_offer_token` 自己回查订单、按策略再算一遍 30% 上限——签名只证明「这是我们签的」，不证明「这个金额当时算对了」。

## 🤝 体验不变量

写在 `config.EXPERIENCE_INVARIANTS`，由 `guardrails.assert_experience_invariants` 在**每个响应返回前**强制自检，违反即打点告警 + 上报 Langfuse `experience-violation` 分数：

| 不变量 | 含义 |
|---|---|
| `always_offer_escape_hatch` | 每一轮都必须带放弃挽留的出口（`escape_hatch`），被护栏拦下时也要带 |
| `never_block_eligible_return` | 符合规则的退货绝不拒绝、绝不加阻力（美欧消费者保护红线） |
| `decline_must_cite_rule` | 婉拒必须附规则原文 + 至少一个替代方案 |
| `no_retention_when_angry` | 情绪超阈值场景禁止出现任何挽留 offer |
| `max_rounds_enforced` | 最多 2 轮谈判，单会话最多 8 次交互 |

`decline_must_cite_rule` 的断言**认 `action` 而不只认 `status`**：`status="declined"` 只有模板分支会写，光看它的话，任何「走了模型的婉拒」都能从断言底下溜过去。

## 📐 指标口径

`/api/metrics` **同时**上报两个挽留率口径，防止销售话术和 QBR 打架：

- `addressable_deflection_rate`（主口径，对应销售主张 80%）—— 分母已排除破损/错发/超窗口
- `overall_deflection_rate`（全量口径，预期 20–30%）

分母是**会话**不是轮次：一段会话谈了两轮也只算一段，口径取它的最终结局，成本按会话累计值记一次。实现上 `record_conversation` 可重复调用——同一个 `session_id` 再来一次是**改写结局**（撤旧值 + 加新值），而不是多算一段。

配套：强制 10% holdout 对照组（`RS_HOLDOUT_PCT=10`）做增量归因，**60 天**结算窗口，二次退货回冲。分桶走 `session_id` 的 SHA-256 摘要，不用内建 `hash()`——后者每个进程带随机种子，换 worker 或重启一次就会换实验臂，归因直接作废。

商业口径、定价三档与异议应对见 [`docs/GTM-PRICING.md`](docs/GTM-PRICING.md)。

## 📊 验证

两条互不依赖的验证线：**离线 e2e**（默认，一秒跑完）与**真实链路**（需 key）。

```bash
.venv/bin/python -m pytest tests -q          # 36 个 e2e，强制 mock
.venv/bin/python scripts/verify_live.py      # 真 LLM + 真 Langfuse
```

**36 个 e2e 按套件分布**（`tests/test_e2e.py`，全程离线确定性）：

| 套件 | 例数 | 覆盖 |
|---|---|---|
| 五阶段流程 | 3 | S3 必须显式确认订单 · S2 查不到用户 · 确认了别人的订单号被拒 |
| 五个场景 | 7 | 婉拒引规则 · 使用问题不发钱 · 补偿不超 30% · 破损豁免窗口 · 情绪小额秒退 · 情绪大额 P1 + SLA · 薅羊毛冷静期 |
| 四层护栏 | 5 | L2 白名单 · L3 越权承诺 · L3 未批准金额 · L4 伪造 token · L4 执行 + 幂等重放 |
| 体验不变量 | 4 | 每条回复都有出口 · 随时可放弃挽留 · 2 轮上限 · 违反计数为 0 |
| 经济效率 | 7 | 意图永不用大模型 · 规则快路零成本 · 高客单升档 · 婉拒走模板 · 均成本 < 预算 1/10 · 确认不占额度 · 双口径都上报 |
| 回归（Code Review 修掉的 8 个问题） | 10 | 中档情绪的不合规单不得走模型 · 断言认 action · 发火 + 不合规不得挽留 · 冷静期不得报护栏 · 一会话只算一段 · holdout 跨进程稳定 · 重启后仍幂等 · CSAT 越界被拒 · `R-USED` 只约束无理由 · 工单 ID 不撞 |

**最新基线**：

| 项 | 值 | 结果 |
|---|---|---|
| e2e 测试 | **36 / 36** | ✅ |
| 真实链路场景 | **6 / 6**（状态与是否走真模型都对） | ✅ |
| 真实链路护栏 | **3 / 3**（L2 + L3 × 2 全部 422） | ✅ |
| `experience_violations` | **0** | ✅ |
| `avg_cost_per_conversation_usd` | **$0.001102**（case 预算 $0.15 的 0.7%） | ✅ |
| Langfuse trace / score | 上报成功，`client_ready=true` | ✅ |

> **这些数字测的是什么**：e2e 跑在确定性 mock 上，度量的是**流程、策略与护栏规则**是否自洽——`experience_violations=0` 说的是断言没被触发，不是「模型话术一定得体」。模型层面的话术质量与真实延迟由 `verify_live.py` 的小样本承担，两者不互相背书。

跨进程的 holdout 分桶稳定性是**真的 fork 子进程验的**（`test_holdout_bucket_stable_across_processes` 跑三次 `subprocess`），不是靠读代码判断——内建 `hash()` 的随机种子问题只在跨进程时才暴露。

## 🔒 安全

**威胁模型** —— 三类不可信输入：① 用户消息（会进意图与生成 prompt）；② **模型自己的输出**（会变成给用户的承诺）；③ 客户端回传的 `offer_token` 与 `idempotency_key`（会触发真实发钱）。②③是本系统最现实的攻击面：一次「模型被说服承诺全额退款」或一次「token 被改金额重放」就是真金白银的损失。

| 防线 | 措施 | 位置 |
| --- | --- | --- |
| 输入 | Pydantic `extra=forbid` + `message` 长度上限 2000 + `force` 走 `Literal` 白名单（演示参数无法被当成注入通道） | `models.NegotiateRequest` |
| 模型→动作 | 动作空间收窄到白名单 `offer_id`，**输出结构里没有金额字段**；`tool_choice` 强制走 function call，自由文本无处可去 | `models.CopyProposal` · `llm.TOOL` |
| 模型→文本 | 出参正则扫金额 / 百分比 / 越权承诺（中英双语），未批准金额一律拦；批准集合只含本次 offers 的真实金额 | `guardrails.scan_output_text` |
| 金额 | 三道独立计算：策略引擎生成时夹 `min(…, 30%)` → L2 校验 → L4 执行层回查订单重算 | `policy` · `guardrails` |
| 执行 | HMAC-SHA256 签名（`compare_digest` 定时比较）+ TTL 15min + `jti` + 幂等键内存与库双查 + 库层唯一约束兜底 | `guardrails.verify_offer_token` · `db/schema.sql` |
| 写接口 | `POST /api/csat` 的 `score` 在接口层框死 `1..5`——无鉴权写接口放个 `99` 就能带偏对外指标 | `app.csat` |
| 降级 | Langfuse / Postgres / LLM 任一不可用都只降级不抛错；埋点与落库**绝不拖垮主流程** | `observability` · `db` · `llm` |

**密钥**：`.env` 全程 gitignore，仅 `.env.example` 入库。`GET /health` 只报「是否配置」，**绝不回显任何密钥本身**。生产环境若仍用默认签名密钥，`config.py` 在 import 时就 `raise`——`RS_ENV=prod` + 默认 `RS_SIGNING_KEY` 直接起不来。

**验证**：L2/L3/L4 各自有 e2e 用例把拦截设为断言；`verify_live.py` 在**真模型**下把同样三条护栏再验一遍，证明拦截不依赖 mock 的行为。

## 🔭 可观测

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com   # US 区 us.cloud / 自托管填自己的
```

两把 key 齐了才启用，缺任何一把整体降级成 no-op——**埋点绝不能拖垮主流程**。启用后：

- `llm.py` 的客户端自动换成 `langfuse.openai` 的 drop-in，prompt / completion / token / 成本 / 延迟全自动上报
- 每次 `/api/negotiate` 是一个 span，`session_id` 串起整段对话，`customer_id` 作为 user，span 出参补上 `scenario` / `action` / `cost_usd` / `stage`
- 进程退出前 `lifespan` 调 `obs.flush()`——短生命周期容器不 flush 会丢 trace

四个分数（命名按**信号来源**而非期望衡量的东西）：

| 分数名 | 类型 | 含义 |
|---|---|---|
| `user-csat` | NUMERIC | 会话结束用户评分 1–5（`POST /api/csat`） |
| `guardrail-trip` | BOOLEAN | 本次会话是否触发过护栏 |
| `experience-violation` | BOOLEAN | 是否违反体验不变量 |
| `retention-outcome` | CATEGORICAL | `offer_made` / `declined` / `instant_refund` / `escalated` / `released` |

> 必须显式 `Langfuse(...)` 构造一次，把凭证注册进 SDK 客户端表。只调 `get_client()` 拿不到，`langfuse.openai` 的 drop-in 会报 "No Langfuse client has been initialized" 并**静默跳过整条 trace**。

> 测试通过 `tests/conftest.py` 清空 `LANGFUSE_*` 凭证关闭 tracing，保证 hermetic、绝不误发到线上 Langfuse。

## 📁 项目结构

```text
return-saver/
├── README.md
├── requirements.txt          # fastapi / pydantic / openai / langfuse>=4 / psycopg[binary,pool] / pytest
├── .env.example              # 配置模板（LLM / Langfuse / PostgreSQL / 策略阈值）
├── config.py                 # 从 .env 读取全部配置 + summary() 供 /health；prod 用默认签名密钥直接 raise
├── models.py                 # 枚举 + Pydantic Schema —— **L1 收窄动作空间**（CopyProposal 刻意无 amount 字段）
├── store.py                  # mock 数据（7 客户 / 8 订单覆盖全场景）+ 商家规则原文 + 知识库 + 会话
├── policy.py                 # 策略引擎：规则核验 / 场景分类 / 分级退款 / offers 白名单（**金额唯一来源**）
├── llm.py                    # 规则快路 + 小模型意图 + 三档生成路由 + 成本核算 + 思考链与 1210 降级
├── guardrails.py             # L2/L3/L4 + 体验不变量断言 + HMAC offer_token 签发与复核
├── observability.py          # Langfuse 接入（traced OpenAI drop-in / span / 四类 score），未配置则 no-op
├── metrics.py                # 双口径挽留率 / 成本 / 路由分布 / 护栏触发；按会话记账可改写结局
├── db.py                     # PostgreSQL 连接池与三张表读写；memory 模式下整个模块 no-op
├── app.py                    # 五阶段编排 + 7 个 API + 统一出口 _ok()（补不变量字段并自检）
├── db/
│   └── schema.sql            # sessions / executions（幂等唯一约束）/ manual_tickets（带 due_at）
├── web/
│   └── admin/                # Shopify App 风格商户后台（C）：Dashboard / Chats / Policy
├── docs/
│   ├── MODEL-SELECTION.md    # GLM 三档选型实测：可用模型、能力矩阵、三个反直觉发现、成本
│   └── GTM-PRICING.md        # 三条销售主张的口径与证明方式、三档定价、异议应对
├── scripts/
│   └── verify_live.py        # 真实链路验证：6 场景 + 3 护栏 + CSAT→Langfuse，与单测刻意分开
├── tests/
│   ├── conftest.py           # 测试隔离：import config 前清空凭证，强制 mock + memory + holdout=0
│   └── test_e2e.py           # 36 个端到端测试（流程 / 场景 / 护栏 / 体验 / 经济 / 回归）
└── demo.sh                   # 12 段演示脚本（纯 HTTP 客户端，会等服务就绪、连不上时说人话）
```

## 🛍️ 商户后台外壳

`GET /admin` 提供 Return Saver 的商户侧后台外壳。这里的 Shopify 是交付风格要求，
不是平台集成：暂不做 OAuth、App Bridge session token、Billing API，也不建
`rs_shop`。商户身份仍沿用 A 阶段的 `X-API-Key → Principal` 约定。

外壳包含三项主菜单：

| Menu | Follow-up project | Current state |
|---|---|---|
| Dashboard | C2 | Implemented operational cards, routing/scenario charts, guardrail panel, and manual queue summary |
| Chats | C3 | Implemented session list, filters, replay/audit placeholders, and `/admin/api/chats` summary data |
| Policy | C1 | Implemented policy cards, rule list, thresholds, and dry-run/versioning placeholders |

前端是零构建静态文件：`web/admin/index.html`、`app.css`、`app.js`。视觉遵循
Polaris 的嵌入式 App 语言：左侧主导航、顶部 Page header、KPI cards、resource
tables、font-icon menu/chart symbols、Shopify 绿色主按钮；但不依赖 Shopify 平台运行时。

## 🧩 配置

全部配置集中在 `config.py`，从 `.env` 读取。优先级：**真实环境变量 > `.env` > 代码默认值**——容器里注入的 secret 永远赢。`.env` 已在 `.gitignore`，模板见 [`.env.example`](.env.example)。

`GET /health` 如实反映各项配置状态，且**绝不回显任何密钥本身**。

### LLM（OpenAI 兼容端点，默认智谱 GLM）

```bash
RS_LLM_BASE_URL=https://api.z.ai/api/paas/v4/
RS_LLM_API_KEY=<your key>
RS_MODEL_INTENT=glm-4.5-air        # 意图+情绪
RS_MODEL_SMALL=glm-5-turbo         # 常规话术
RS_MODEL_LARGE=glm-4.6             # 高情绪/高客单/二轮僵持
RS_THINKING_INTENT=false           # 三档一律关思考链
RS_THINKING_SMALL=false
RS_THINKING_LARGE=false
RS_MAX_TOKENS_INTENT=256           # 推理模型会把 max_tokens 烧在 reasoning 上，必须留足
RS_MAX_TOKENS_SMALL=512
RS_MAX_TOKENS_LARGE=1024
RS_COST_BUDGET=0.05                # 单会话成本硬预算，超了强制降模板
RS_LATENCY_BUDGET=12               # 超时打 LATENCY_BUDGET_EXCEEDED 告警
```

换厂商只改这几行，代码一行不动。模型名与能力是**实测**选定的，三个反直觉的坑见 [经济效率](#-经济效率两级意图--三档生成路由) 与 [`docs/MODEL-SELECTION.md`](docs/MODEL-SELECTION.md)。

### Langfuse 可观测性

见 [🔭 可观测](#-可观测)。另有 `LANGFUSE_TRACING_ENVIRONMENT`（环境标签，默认对齐 `RS_ENV`）与 `RS_LANGFUSE_SAMPLE_RATE`（生产可降到 0.2 控成本）。

### PostgreSQL

```bash
RS_STORE_BACKEND=postgres          # 默认 memory
DATABASE_URL=postgresql://returnsaver:returnsaver@localhost:5432/returnsaver
RS_DB_POOL_MIN=1
RS_DB_POOL_MAX=10
RS_DB_CONNECT_TIMEOUT=5
```

建库建表：

```bash
psql -d postgres -c "CREATE ROLE returnsaver LOGIN PASSWORD 'returnsaver';" \
                 -c "CREATE DATABASE returnsaver OWNER returnsaver;"
psql "$DATABASE_URL" -f db/schema.sql
```

**只持久化丢了会出事的三张表**——订单与客户仍走 mock（case 要求不接真 Shopify）：

| 表 | 丢了会怎样 |
|---|---|
| `sessions` | 用户要重新确认订单 |
| `executions` | **重复发钱**（幂等键唯一约束兜底） |
| `manual_tickets` | 违反 SLA 承诺（带 `due_at`，`/api/manual-queue` 直接算 `sla_breached`） |

内存永远是真相源，Postgres 用于重启恢复与离线分析。**库连不上时所有写操作静默降级**，主流程不受影响（`/health` 会如实报 `ok:false` + 错误原因）。

> `due_at` 不能用生成列：`timestamptz + interval` 是 STABLE 而非 IMMUTABLE，Postgres 会拒绝建表。改在 INSERT 时用 `make_interval` 算好。

### 多租户与配置来源

策略阈值与规则原文是**租户级**的，存在 Postgres 的 `rs_merchant_config`（append-only 版本化表），不再由 `.env` 决定。下一节那些 `RS_*` 变量现在只是**内置默认值**——库里有该租户的配置时以库为准。

```bash
RS_API_KEYS={"sk-svc":"public","sk-alice":"public:C-001"}   # 留空 = dev 模式（单租户）
RS_DEFAULT_TENANT=public
RS_DEFAULT_CUSTOMER=
```

三类配置：

| 类别 | 谁能改 | 内容 |
|---|---|---|
| 商家可改 | 商家 | 10 个业务阈值 + `exceptions_note` + 4 条规则原文 |
| 随套餐下发，商家只读 | 平台 | `plan`（`starter`/`pro`/`advanced`）· `cost_budget_usd`（0.02/0.05/0.15，天花板 0.30） |
| 平台全局 | 平台（`.env`） | 模型/定价表/TTL/签名密钥/Langfuse/DB/holdout |

**`EXPERIENCE_INVARIANTS` 完全不暴露**——五条体验不变量是平台法律，商家连关掉的入口都没有。其余商家阈值受**平台硬天花板**约束（让利上限 ≤ 50%、`emotion_hard_stop ∈ [0.5,0.9]`、`emotion_large_model < emotion_hard_stop`、谈判轮次 ≤ 3……），越界写入被拒，手插的越界行在读取时**逐字段 clamp**（一个字段填错不该让商家丢掉其余设置）。`R-DAMAGE`（破损豁免）不可停用，库层 CHECK 也拦着。

**会话快照**：会话开始时钉住 `config_version`，`offer_token` 载荷带上它，L4 按**签发时那一版**复核上限。所以商家中途调低让利上限，不会否掉系统已经承诺给客户的方案——但也不是免检，超过签发那一版的上限照样拦。

建表与 seed：

```bash
psql "$DATABASE_URL" -f db/schema.sql                        # 会话/幂等/工单三张表
psql "$DATABASE_URL" -f db/migrations/001_multitenant_config.sql  # 五张 rs_ 表 + seed v1
psql "$DATABASE_URL" -f db/migrations/002_demo_fixtures.sql   # 演示数据，生产不跑
```

零配置仍然成立：没有库或 `RS_STORE_BACKEND=memory` 时走内置默认配置，hermetic 测试与离线演示不受影响。`/health` 的 `config.source` 会如实报 `builtin` 还是 `database`。

### 策略与体验阈值

> 以下默认值在接了库之后只是**内置兜底**，租户的实际取值来自 `rs_merchant_config`。

| 变量 | 默认 | 说明 |
|---|---|---|
| `RS_MAX_NEGOTIATION_ROUNDS` | `2` | 最多谈几轮，之后无条件放行退货 |
| `RS_MAX_SESSION_TURNS` | `8` | 单会话交互硬上限 |
| `RS_MAX_DISCOUNT_PCT` | `0.30` | 任何让利 ≤ 订单金额的比例上限（L2/L4 都按这个复核） |
| `RS_INSTANT_REFUND_CAP` | `50` | 情绪激烈 + 金额 ≤ 此值且无异常 → 立即退款 |
| `RS_MANUAL_SLA_HOURS` | `2` | 转人工的响应时效承诺，写进工单 `due_at` |
| `RS_EMOTION_HARD_STOP` | `0.70` | ≥ 此值禁止再挽留（判定优先于合规性） |
| `RS_EMOTION_LARGE_MODEL` | `0.45` | ≥ 此值生成侧升大模型 |
| `RS_HIGH_VALUE_ORDER` | `200` | ≥ 此金额生成侧升大模型 |
| `RS_INTENT_RULE_CONFIDENCE` | `0.85` | 规则快路可信阈值，≥ 此值就不调模型 |
| `RS_ABUSE_NEGOTIATION_LIMIT` | `3` | 90 天内谈判 ≥ 此值进冷静期，放行标准退货不再给券 |
| `RS_OFFER_TOKEN_TTL` | `900` | `offer_token` 有效期（秒） |
| `RS_HOLDOUT_PCT` | `0` | **生产置 10**：对照组，用于挽留效果的增量归因 |
| `RS_SIGNING_KEY` | dev 占位 | HMAC 签名密钥；`RS_ENV=prod` 时仍用默认值会直接 raise |

## 🗺️ 路线图

- ✅ 五阶段状态机 + 五场景确定性策略引擎（`policy.py` 为金额唯一来源）
- ✅ 四层护栏 L1→L4：动作空间收窄 / Schema + 白名单 + 30% 上限 / 出参文本扫描 / HMAC token + 幂等 + 独立复核
- ✅ 体验不变量断言（出口 / 婉拒引规则 / 发火不挽留 / 轮次上限），违反即打点上报
- ✅ 两级意图 + 三档生成路由 + 会话成本硬预算（实测均成本为 case 预算的 0.7%）
- ✅ `.env` 配置层（LLM / Langfuse / Postgres / 策略阈值），优先级真实环境变量 > `.env` > 默认
- ✅ 真实链路打通：z.ai GLM 三档选型实测（思考链 / 1210 / max_tokens 三个坑）+ Langfuse trace 与四类 score 验证
- ✅ PostgreSQL 三张表落库（会话 / 执行幂等 / 人工工单），memory 模式下完全 no-op
- ✅ 36 个 e2e（含 Code Review 修掉的 8 个问题的回归用例）+ `verify_live.py` 真实链路验证
- ✅ 双口径挽留率 + 强制 holdout 分桶（SHA-256，跨进程稳定，有子进程测试兜底）
- ✅ Shopify App 风格商户后台（C）：`/admin` 按 Dashboard / Chats / Policy 顺序提供三项管理界面
- ⬜ **流式输出**（首字延迟压到 1–2s）—— `glm-4.6` 跨次实测 9.7–13.5s，逼近甚至超出 12s 预算，MVP 之后第一优先级
- ⬜ `load_session` 接回读路径，重启后从库恢复会话而非从内存重建
- ⬜ `MODEL_PRICING` 换成厂商合同价（当前是量级占位值）
- ⬜ 意图侧语义缓存（同义问法命中率高，可再削掉一部分小模型调用）
- ⬜ 多语言情绪词典（西/法/德语）· 人工工单接 Zendesk / Gorgias · 接真 Shopify 订单源

## ⚠️ 已知边界

MVP 范围外，但都是上线前必须收掉的：

- **`glm-4.6` 延迟跨次实测 9.7–13.5s，逼近甚至超出 12s 预算**，超出即打 `LATENCY_BUDGET_EXCEEDED` 告警。下一步必须上流式输出——在聊天窗口里，多等 10 秒比话术差一点严重得多。
- `MODEL_PRICING` 是量级占位值；成本的**相对结构**（零调用占比、三档分布）是实测的，绝对值不是。
- Postgres 已实测落库；但 `load_session` 尚未接回读路径，重启后仍从内存重建会话。
- Langfuse 分数要查 `/api/public/v2/scores`（v1 端点返回空），且摄入有 ~15s 最终一致延迟。
- 情绪识别靠词典 + 小模型，未做多语言（跨境场景需补西/法/德语词典）。
- 人工工单队列在 memory 模式下是内存 list，未接 Zendesk / Gorgias。
- 订单与客户是 mock（case 明确要求不接真 Shopify），`store.py` 的接口签名已按可替换设计。

## 📄 许可

本仓库是 case 的交付实现，未附开源许可证。引用或复用请注明出处。
