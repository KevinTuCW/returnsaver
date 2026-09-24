<div align="center">

# 🛟 Return Saver

**退货挽留 AI Agent** —— 确定性策略负责决策与金额，LLM 只负责措辞

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Pydantic](https://img.shields.io/badge/Pydantic-v2-e92063.svg)](https://docs.pydantic.dev/)
[![Langfuse](https://img.shields.io/badge/Langfuse-v4%20tracing-fbbf24.svg)](#-可观测)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-optional-336791.svg?logo=postgresql&logoColor=white)](#postgresql)
[![tests](https://img.shields.io/badge/tests-177%20passed-brightgreen.svg)](#-验证)
[![guardrails](https://img.shields.io/badge/护栏-4%20层%20L1→L4-brightgreen.svg)](#-设计原则)
[![experience violations](https://img.shields.io/badge/体验不变量违反-0-brightgreen.svg)](#-设计原则)
[![cost](https://img.shields.io/badge/单次会话成本-0.7%25%20of%20budget-brightgreen.svg)](#-设计原则)

面向跨境 DTC Shopify 品牌 · chat widget 检测到退货意图后调用本服务

</div>

---

Return Saver 在**不损伤用户体验**的前提下挽留退货。流程被拆成五个可测试阶段，五类场景分别走确定性策略；**金额与动作只从策略引擎产生**，模型只负责措辞。没有 key 时自动使用确定性 mock、no-op tracing 和内存存储，可离线运行完整流程。

**两条不可违背的主张**

1. **LLM 有建议权，没有执行权** —— 金额与动作只从确定性策略引擎出，四层护栏兜底。模型的输出结构里**根本没有金额字段**。
2. **降退货率不得以伤体验换** —— 体验约束写成会抛错的断言（`guardrails.assert_experience_invariants`），不是 prompt 里的建议。

`scripts/verify_live.py` 验证 6 个真实场景、3 条护栏与 CSAT 上报。最近一次实测均成本为 `$0.001102`/会话，路由分布为 `none 13 / large 2 / small 1`；规则快路与确定性模板是主要节省来源。

## 🖥️ 操作界面

### 用户端

Helpmate 内嵌 Return Saver，客户可以确认订单、查看挽留方案并继续标准退货流程。

![Return Saver customer experience](images/RS%20customer%20side.jpeg)

### 商家端

商家在 Return Saver Admin 中查看经营指标与历史咨询，并维护售后规则。

| Dashboard | Chats | Policy |
|---|---|---|
| ![Merchant dashboard](images/RS%20merchant%20dashboard.jpeg) | ![Merchant chats](images/RS%20merchant%20chats.jpeg) | ![Merchant policy](images/RS%20merchant%20policy.jpeg) |

## 📑 目录

- [🖥️ 操作界面](#️-操作界面)
- [✨ 特性](#-特性)
- [🏗️ 架构](#️-架构)
- [🧱 技术栈](#-技术栈)
- [🚀 快速开始](#-快速开始)
- [🔌 接口](#-接口)
- [💬 使用示例](#-使用示例)
- [🧭 设计原则](#-设计原则)
- [📐 指标口径](#-指标口径)
- [📊 验证](#-验证)
- [🔒 安全](#-安全)
- [🔭 可观测](#-可观测)
- [🛍️ 商户后台](#️-商户后台)
- [🧩 配置](#-配置)
- [🗺️ 路线图](#️-路线图)
- [⚠️ 已知边界](#️-已知边界)
- [📄 许可](#-许可)

## ✨ 特性

- 🔻 **五阶段状态机** —— 意图识别 → 身份订单校验 → 确认订单 → 规则核验 → 场景执行；S3 未确认订单绝不执行。
- 🎯 **五场景策略引擎** —— 不合规、使用问题、价值不符、产品损坏、情绪激烈分别走确定性策略。
- 🛡️ **模型不掌握执行权** —— 模型只能选择白名单 `offer_id` 并生成文案；金额由策略产生，执行只认签名 token。
- 🤝 **体验约束可执行** —— 每轮提供退出挽留入口，婉拒引用规则，愤怒用户不再挽留，并限制轮次和交互数。
- 💰 **两级意图 + 三档生成路由** —— 意图侧永不调用大模型（规则快路命中即零调用）；生成侧按情绪 / 客单 / 轮次 / VIP 动态选 `none`（模板，$0）/ `small` / `large`。实测均成本 **$0.0011 / 次会话 = case 预算的 0.7%**。
- 🔌 **零配置即可完整演示** —— 没 key 时 LLM 自动降级到确定性 mock、Langfuse 变 no-op、存储走内存；模型抖动、空 content、`1210` 强制思考报错全部有降级路径，绝不让用户卡住。
- 📐 **双口径挽留率** —— `/api/metrics` 同时上报 `addressable_deflection_rate`（主口径，分母已排除破损/错发/超窗口）与 `overall_deflection_rate`（全量），防止销售话术和 QBR 打架。分母是**会话**不是轮次。
- 🧪 **强制 holdout 对照组** —— `RS_HOLDOUT_PCT=10` 做增量归因，60 天结算窗口。分桶走 `session_id` 的 SHA-256 摘要，不用内建 `hash()`——后者每进程带随机种子，换 worker 就换实验臂，归因直接作废。
- 🔭 **Langfuse 全链路 + 四类分数** —— 一次 `/api/negotiate` 一个 span，`session_id` 串起整段对话；`user-csat` / `guardrail-trip` / `experience-violation` / `retention-outcome` 四个分数按**信号来源**命名。
- 💾 **持久化关键业务状态** —— 会话、执行幂等、人工工单、租户规则及 Admin 历史数据均可落 PostgreSQL；不可用时降级到内存。

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

## 🧱 技术栈

| 关注点 | 选型 | 理由 |
| :--- | :--- | :--- |
| AI&nbsp;coding | Claude Code + Codex | — |
| API&nbsp;/&nbsp;编排 | **FastAPI** + Pydantic v2 | **护栏即 Schema**：`extra=forbid` + 字段约束就是 L1/L2 的实现 |
| LLM | **GLM 三档**（`glm-4.5-air` 意图 / `glm-5-turbo` 常规 / `glm-4.6` 高价值），OpenAI 兼容端点 | 换厂商只改 `.env` 三行，代码一行不动；模型名与能力是**实测**选定，见 [`docs/MODEL-SELECTION.md`](docs/MODEL-SELECTION.md) |
| 生成&nbsp;契约 | OpenAI **function calling**（`tool_choice` 强制） | 让模型只能产出 `{offer_id, message}`，自由文本无处可去 |
| 可观测 | **Langfuse v4**（`langfuse.openai` drop-in + scores） | 换个 import 就有 prompt/token/成本/延迟全链路 |
| 执行层&nbsp;信任 | **HMAC-SHA256** 签名 token（TTL 15min + `jti`） | 动钱这一步不信任上游任何输入，只信自己的签名 |
| 数据 | 内存（默认）/ **PostgreSQL 16**（可选） | 会话恢复、幂等、工单、租户配置与历史咨询持久化 |
| 测试 | pytest + `TestClient` | 177 个离线测试，不花钱、不受模型波动影响 |
| 部署 | Railway / Fly.io | — |

> 所有 LLM 调用走 OpenAI 兼容协议；不填 key 时全程确定性 mock，不触碰网络。

## 🚀 快速开始

**前置**：Python 3.12+。默认运行时完全离线、确定性，无需任何 key。

```bash
make install               # 创建 .venv 并安装依赖
cp .env.example .env       # 可选：填写 LLM、Langfuse、PostgreSQL 配置
make test                  # 177 passed，强制 mock、不访问网络
make run                   # http://localhost:8777/admin
```

可通过 `PORT=8788 make run` 指定端口。真实 LLM 与 Langfuse 链路使用 `.venv/bin/python scripts/verify_live.py` 验证。

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
| `GET` | `/admin` | Shopify Admin 风格商户后台：Dashboard / Chats / Policy |
| `GET` | `/admin/api/dashboard` | Dashboard 指标、趋势与结果分布 |
| `GET` | `/admin/api/chats` | 可筛选的历史咨询和完整会话记录 |


`/api/negotiate` 的关键入参：`session_id`（续会话）· `customer_id` · `message` · `confirm_order_id`（S3 确认）· `want_return_anyway`（体验出口，随时放弃挽留）· `force`（**仅演示护栏**：`bad_offer` / `bad_text` / `bad_amount`）。

`score` 的范围必须在接口层就框死——这是个无鉴权的写接口，放一个 `99` 进来就能把 `avg_csat` 这条对外指标彻底带偏。

配了 `RS_API_KEYS` 后 `/api/negotiate` 与 `/api/accept` 都要求 `X-API-Key`，**身份只来自凭证，永不来自请求体**。key 映射到 `"tenant"` 或 `"tenant:customer"`：

- **租户级 key**（`"public"`）—— 服务间调用用这种。客户主体由上游（helpmate）鉴权后透传在请求体的 `customer_id` 里。
- **客户级 key**（`"public:C-001"`）—— 只能代表那一个客户说话，请求体换成别的客户直接 **403**。

会话是租户级资源：拿 B 租户的 key 续 A 租户的会话也是 **403**。

## 💬 使用示例

先请求候选订单，再携带 `session_id` 与 `confirm_order_id` 推进到方案阶段：

```bash
curl -s --noproxy '*' -X POST localhost:8777/api/negotiate \
  -H 'Content-Type: application/json' \
  -d '{"customer_id":"C-001","message":"It is too small, I want a return"}'
```

```bash
curl -s --noproxy '*' -X POST localhost:8777/api/negotiate \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"S-db4b16a8bd43","customer_id":"C-001",
       "message":"It is too small, I want a return","confirm_order_id":"ORD-1001"}'
```

响应包含 `status`、`stage`、`reply`、白名单 `offer`、签名 `offer_token`、备选方案和模型成本。未确认订单前 `offer` 必须为空；金额由系统渲染，模型文案不得包含金额。

护栏演示参数 `force` 可验证 L2/L3 拦截：

```bash
curl -s --noproxy '*' -X POST localhost:8777/api/negotiate \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"S-…","customer_id":"C-001","message":"too small",
       "confirm_order_id":"ORD-1001","force":"bad_text"}'
```

HTTP 422 响应会返回具体 `guardrail.code`，并保留标准退货出口。接受方案时使用服务端签发的 token；同一个幂等键重放不会重复执行：

```bash
curl -s --noproxy '*' -X POST localhost:8777/api/accept \
  -H 'Content-Type: application/json' \
  -d '{"offer_token":"7b2265787022…","idempotency_key":"k-0001"}'
# 首次返回 executed；相同 key 重放返回 already_executed
```

## 🧭 设计原则

### 状态机与场景

| 阶段 | 做什么 | 归属 | 关键设计 |
|---|---|---|---|
| **S1&nbsp;意图识别** | 意图 + 退货原因 + 情绪分 | 规则快路 → **小模型** | 永不调用大模型；情绪超阈值由规则直接定，不交给模型 |
| **S2&nbsp;信息校验** | 用户身份、订单归属、可退候选 | **[Code]** | 查不到就要身份信息，不猜 |
| **S3&nbsp;确认订单** | 列候选，等用户显式确认 | **[Code]** | 不确认绝不进入执行——认错单就是动错钱 |
| **S4&nbsp;规则核验** | 窗口 / final sale / 卫生 / 使用痕迹 | **[Code]** | 破损与质量走豁免通道；`R-USED` 只约束无理由退货 |
| **S5&nbsp;场景执行** | 分类 → 选方案 → 生成话术 → 护栏 | **[Code]** 决策 / **[LLM]** 措辞 | 金额只从策略引擎出 |

确认订单不占用谈判额度；`round` 只统计实际发出的挽留方案。

| 场景 | 触发 | 策略 | 是否花钱 |
|---|---|---|---|
| **不满足售后规则** | 超窗口 / final sale / 卫生类 | 附**规则原文**婉拒 + 必给替代方案（维修或小额券） | 极少 |
| **产品使用问题** | 不会用 / 连不上 / 预期错位 | 手册 + 视频 + 三条技巧 + 窗口延长 14 天 + 1v1 指导 | **否** |
| **价格价值不符** | 尺码不合 / 觉得不值 / 不想要 | 免费换码优先，其次阶梯 credit（15% → 25%） | 是，≤30% |
| **产品损坏** | 破损 / 错发 / 质量 | 免费维修 → 免费换新 → 带瑕疵保留补差 | 视情况 |
| **情绪激烈执意退** | 情绪分 ≥ 0.70 | **分级**：≤$50 且无异常 → 立即退；否则 → 人工介入 P1/P2 + 2h SLA | 退款 |

情绪判定优先于合规性：高情绪用户不再接收挽留方案；不合规且高情绪的请求转人工复核。风险用户或 90 天内谈判达到阈值时直接放行标准退货，不再发券。

### 模型路由

意图侧先走关键词与情绪词典，未命中才调用小模型分类；生成侧按业务价值选择模板、小模型或大模型：

**生成侧动态路由**

| 档位 | 触发条件 | 成本 |
|---|---|---|
| `none`（模板） | 婉拒场景（**不看情绪**）、情绪激烈场景、会话预算耗尽 | **$0** |
| `small` | 默认 | 低 |
| `large` | 情绪 ≥0.45 / 订单 ≥$200 / 第二轮僵持 / VIP 且 ≥$80 | 高但值得 |

单会话超过 `RS_COST_BUDGET` 后强制使用模板。模型选型、延迟与成本实测见 [`docs/MODEL-SELECTION.md`](docs/MODEL-SELECTION.md)。

### 护栏与体验不变量

| 层 | 机制 | 位置 | 演示 |
|---|---|---|---|
| **L1&nbsp;收窄动作空间** | LLM 只能 function call `propose_copy(offer_id ∈ 白名单)`；**结构里没有金额字段**，「退 200%」在语法上不可表达 | `models.py` · `llm.TOOL` | — |
| **L2&nbsp;Schema&nbsp;+&nbsp;策略校验** | Pydantic `extra=forbid` + 白名单断言 + `value ≤ 订单 30%` | `guardrails.validate_proposal` / `validate_value_cap` | `force:"bad_offer"` → 422 `OFFER_NOT_IN_ALLOWLIST` |
| **L3&nbsp;出参文本扫描** | 正则扫金额/百分比/`keep the product`/`refund you`/`全额退款`，未批准一律拦 | `guardrails.scan_output_text` | `force:"bad_text"` → `FORBIDDEN_PHRASE`；`force:"bad_amount"` → `UNAPPROVED_AMOUNT` |
| **L4&nbsp;执行层隔离** | 动钱只认 HMAC `offer_token`（TTL 15min + `jti` + 幂等键**内存与库双查**，重启后仍幂等），并**独立复核**上限 | `guardrails.issue_offer_token` / `verify_offer_token` · `app.accept` | 伪造 token → `BAD_SIGNATURE`；重放 → `already_executed` |

L4 同时验证签名、TTL、唯一 `jti`、持久化幂等键，并回查订单独立复核金额上限。体验不变量由 `guardrails.assert_experience_invariants` 在每个响应返回前检查：

| 不变量 | 含义 |
|---|---|
| `always_offer_escape_hatch` | 每一轮都必须带放弃挽留的出口（`escape_hatch`），被护栏拦下时也要带 |
| `never_block_eligible_return` | 符合规则的退货绝不拒绝、绝不加阻力（美欧消费者保护红线） |
| `decline_must_cite_rule` | 婉拒必须附规则原文 + 至少一个替代方案 |
| `no_retention_when_angry` | 情绪超阈值场景禁止出现任何挽留 offer |
| `max_rounds_enforced` | 最多 2 轮谈判，单会话最多 8 次交互 |

违反不变量会记录指标并上报 Langfuse `experience-violation` 分数。

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
.venv/bin/python -m pytest tests -q          # 177 passed，强制 mock
.venv/bin/python scripts/verify_live.py      # 真 LLM + 真 Langfuse
```

**最新基线**：

| 项 | 值 | 结果 |
|---|---|---|
| 自动化&nbsp;测试 | **177 / 177** | ✅ |
| 真实链路&nbsp;场景 | **6 / 6**（状态与是否走真模型都对） | ✅ |
| 真实链路&nbsp;护栏 | **3 / 3**（L2 + L3 × 2 全部 422） | ✅ |
| `experience_violations` | **0** | ✅ |
| `avg_cost_per_conversation_usd` | **$0.001102**（case 预算 $0.15 的 0.7%） | ✅ |
| Langfuse&nbsp;trace&nbsp;/&nbsp;score | 上报成功，`client_ready=true` | ✅ |

离线测试验证流程、策略、Admin API、持久化和护栏；模型话术质量、真实延迟与 Langfuse 上报由 `verify_live.py` 单独验证。

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

## 🛍️ 商户后台

`GET /admin` 提供英文版 Shopify Admin 风格界面。当前不依赖 Shopify OAuth、App Bridge 或 Billing API，商户身份沿用 `X-API-Key → Principal`。

| Menu | 能力 |
|---|---|
| Dashboard | 售后接管、订单处理、预计挽留、转人工、时长和成本指标；支持日/周/月趋势与结果分布 |
| Chats | 按时间、客户、产品、订单和关键词筛选，选中客户后查看完整历史会话 |
| Policy | 配置全局规则、特殊产品/场景规则及按订单金额和挽留次数触发的退款审核规则 |

前端位于 `web/admin/`，使用紧凑的 Polaris 式布局、font-icon 菜单和 Seel 风格强调色。

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

换厂商只需修改端点、key 和模型名。选型实测见 [`docs/MODEL-SELECTION.md`](docs/MODEL-SELECTION.md)。

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

`db/schema.sql` 保存三类核心运行状态：

| 表 | 丢了会怎样 |
|---|---|
| `sessions` | 用户要重新确认订单 |
| `executions` | **重复发钱**（幂等键唯一约束兜底） |
| `manual_tickets` | 违反 SLA 承诺（带 `due_at`，`/api/manual-queue` 直接算 `sla_breached`） |

PostgreSQL 启用后负责会话恢复、跨重启幂等和 Admin 历史记录；不可用时主流程降级到内存，`/health` 会报告连接状态。

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

`EXPERIENCE_INVARIANTS` 不向商家开放。商家阈值受平台硬上限约束，越界写入会被拒；`R-DAMAGE` 破损豁免不可停用。

**会话快照**：会话开始时钉住 `config_version`，`offer_token` 载荷带上它，L4 按**签发时那一版**复核上限。所以商家中途调低让利上限，不会否掉系统已经承诺给客户的方案——但也不是免检，超过签发那一版的上限照样拦。

建表与 seed：

```bash
psql "$DATABASE_URL" -f db/schema.sql                        # 会话/幂等/工单三张表
psql "$DATABASE_URL" -f db/migrations/001_multitenant_config.sql  # 五张 rs_ 表 + seed v1
psql "$DATABASE_URL" -f db/migrations/002_demo_fixtures.sql      # 演示订单与客户
psql "$DATABASE_URL" -f db/migrations/003_helpmate_demo_bridge.sql
psql "$DATABASE_URL" -f db/migrations/004_persistent_admin_history.sql
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

- ⬜ **流式输出**（首字延迟压到 1–2s）—— `glm-4.6` 跨次实测 9.7–13.5s，逼近甚至超出 12s 预算，MVP 之后第一优先级
- ⬜ `MODEL_PRICING` 换成厂商合同价（当前是量级占位值）
- ⬜ 意图侧语义缓存（同义问法命中率高，可再削掉一部分小模型调用）
- ⬜ 多语言情绪词典（西/法/德语）· 人工工单接 Zendesk / Gorgias · 接真 Shopify 订单源

## ⚠️ 已知边界

MVP 范围外，但都是上线前必须收掉的：

- **`glm-4.6` 延迟跨次实测 9.7–13.5s，逼近甚至超出 12s 预算**，超出即打 `LATENCY_BUDGET_EXCEEDED` 告警。下一步必须上流式输出——在聊天窗口里，多等 10 秒比话术差一点严重得多。
- `MODEL_PRICING` 是量级占位值；成本的**相对结构**（零调用占比、三档分布）是实测的，绝对值不是。
- Langfuse 分数要查 `/api/public/v2/scores`（v1 端点返回空），且摄入有 ~15s 最终一致延迟。
- 情绪识别靠词典 + 小模型，未做多语言（跨境场景需补西/法/德语词典）。
- 人工工单队列在 memory 模式下是内存 list，未接 Zendesk / Gorgias。
- 订单与客户是 mock（case 明确要求不接真 Shopify），`store.py` 的接口签名已按可替换设计。

## 📄 许可

本项目采用 [MIT License](LICENSE) 开源许可。
