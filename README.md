# Return Saver — 退货挽留 AI Agent（MVP）

面向跨境 DTC Shopify 品牌。chat widget 检测到退货意图后调用本服务，在**不损伤用户体验**的前提下尽量把这单救回来。

**两条不可违背的主张**
1. **LLM 有建议权，没有执行权** —— 金额与动作只从确定性策略引擎出，四层护栏兜底。
2. **降退货率不得以伤体验换** —— 体验约束写成会抛错的断言，不是 prompt 里的建议。

---

## 跑起来

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env               # 填 key；不填也能跑
.venv/bin/python -m uvicorn app:app --port 8777
bash demo.sh                        # 12 段全流程演示
.venv/bin/python -m pytest tests -q # 26 个 e2e 测试（强制 mock，不走网络）
.venv/bin/python scripts/verify_live.py  # 真实 LLM + Langfuse 链路验证
```

测试与真实链路验证**刻意分开**：`tests/conftest.py` 清空凭证强制走 mock，保证测试 1 秒跑完、不花钱、不因模型抖动假失败；真实链路由 `verify_live.py` 单独验。

**零配置即可完整演示**：没 key 时 LLM 自动降级到确定性 mock、Langfuse 变 no-op、存储走内存，断网/限流都不翻车。

> 本机若开着代理，`curl` 需加 `--noproxy '*'`，否则 localhost 会被代理劫持。

## 配置（`.env`）

全部配置集中在 `config.py`，从 `.env` 读取。优先级：**真实环境变量 > `.env` > 代码默认值**——容器里注入的 secret 永远赢。`.env` 已在 `.gitignore`，模板见 [`.env.example`](.env.example)。

`GET /health` 如实反映各项配置状态，且**绝不回显任何密钥本身**。

### LLM（OpenAI 兼容端点，默认智谱 GLM）

```bash
RS_LLM_BASE_URL=https://api.z.ai/api/paas/v4/
RS_LLM_API_KEY=<your key>
RS_MODEL_INTENT=glm-4.5-air        # 意图+情绪
RS_MODEL_SMALL=glm-5-turbo         # 常规话术
RS_MODEL_LARGE=glm-4.6             # 高情绪/高客单/二轮僵持
RS_THINKING_INTENT=false           # 三档一律关思考链，见下
RS_THINKING_SMALL=false
RS_THINKING_LARGE=false
RS_COST_BUDGET=0.05                # 单会话成本硬预算，超了强制降模板
RS_LATENCY_BUDGET=12               # 超时打告警
```

换厂商只改这几行，代码一行不动。模型名与能力是**实测**选定的，详见 [`docs/MODEL-SELECTION.md`](docs/MODEL-SELECTION.md)——三个反直觉的坑：

- 旧文档里的 `glm-4-flash` / `glm-4-air` 在 z.ai 端点**不存在**
- **「flash」档比「air」档贵**：`glm-5.3-flash*` 强制思考关不掉（错误码 1210），分类任务烧 126–152 token，`glm-4.5-air` 关思考链只要 41
- **思考链对挽留话术是纯负收益**：延迟翻倍（19–22s → 9–10s）、token 涨 3 倍，话术几乎一样

### Langfuse 可观测性

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com   # US 区 us.cloud / 自托管填自己的
```

两把 key 齐了才启用，缺任何一把整体降级成 no-op——**埋点绝不能拖垮主流程**。启用后：

- `llm.py` 的客户端自动换成 `langfuse.openai` 的 drop-in，prompt/completion/token/成本/延迟全自动上报
- 每次 `/api/negotiate` 是一个 span，`session_id` 串起整段对话，`customer_id` 作为 user
- 四个分数（命名按**信号来源**而非期望衡量的东西）：

| 分数名 | 类型 | 含义 |
|---|---|---|
| `user-csat` | NUMERIC | 会话结束用户评分 1–5（`POST /api/csat`） |
| `guardrail-trip` | BOOLEAN | 本次会话是否触发过护栏 |
| `experience-violation` | BOOLEAN | 是否违反体验不变量 |
| `retention-outcome` | CATEGORICAL | `offer_made` / `declined` / `instant_refund` / `escalated` / `released` |

### PostgreSQL

```bash
RS_STORE_BACKEND=postgres          # 默认 memory
DATABASE_URL=postgresql://returnsaver:returnsaver@localhost:5432/returnsaver
RS_DB_POOL_MIN=1
RS_DB_POOL_MAX=10
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

---

## 一、退货流程：五阶段状态机

| 阶段 | 做什么 | 归属 | 关键设计 |
|---|---|---|---|
| **S1 意图识别** | 意图 + 退货原因 + 情绪分 | 规则快路 → **小模型** | 永不调用大模型；情绪超阈值由规则直接定，不交给模型 |
| **S2 信息校验** | 用户身份、订单归属、可退候选 | **[Code]** | 查不到就要身份信息，不猜 |
| **S3 确认订单** | 列候选，等用户显式确认 | **[Code]** | 不确认绝不进入执行——认错单就是动错钱 |
| **S4 规则核验** | 窗口 / final sale / 卫生 / 使用痕迹 | **[Code]** | 破损与质量走豁免通道，不受窗口限制 |
| **S5 场景执行** | 分类 → 选方案 → 生成话术 → 护栏 | **[Code]** 决策 / **[LLM]** 措辞 | 金额只从策略引擎出 |

确认订单**不占用**谈判额度；`round` 只统计真正发出过的挽留轮次。

## 二、五个场景与对应策略

| 场景 | 触发 | 策略 | 是否花钱 |
|---|---|---|---|
| **不满足售后规则** | 超窗口 / final sale / 卫生类 | 附**规则原文**婉拒 + 必给替代方案（维修或小额券） | 极少 |
| **产品使用问题** | 不会用 / 连不上 / 预期错位 | 手册 + 视频 + 三条技巧 + 窗口延长 14 天 + 1v1 指导 | **否** |
| **价格价值不符** | 尺码不合 / 觉得不值 / 不想要 | 免费换码优先，其次阶梯 credit（15% → 25%） | 是，≤30% |
| **产品损坏** | 破损 / 错发 / 质量 | 免费维修 → 免费换新 → 带瑕疵保留补差 | 视情况 |
| **情绪激烈执意退** | 情绪分 ≥ 0.70 | **分级**：≤$50 且无异常 → 立即退；否则 → 人工介入 P1/P2 + 2h SLA | 退款 |

情绪激烈场景 **禁止任何挽留话术**（`allow_retention=False`，违反会被断言拦下）。立即退款附带 `comeback_credit` 回头钩子——把一次差体验变成下次再来的理由。

## 三、经济效率：两级意图 + 三档生成路由

**意图侧**（永不用大模型）
1. 规则快路：关键词 + 情绪词典命中 → **零模型调用**
2. 未命中 → 小模型 JSON 分类

**生成侧动态路由**

| 档位 | 触发条件 | 成本 |
|---|---|---|
| `none`（模板） | 婉拒场景、情绪激烈场景、会话预算耗尽 | **$0** |
| `small` | 默认 | 低 |
| `large` | 情绪 ≥0.45 / 订单 ≥$200 / 第二轮僵持 / VIP 且 ≥$80 | 高但值得 |

**真实 LLM 实测**（`scripts/verify_live.py`，6 个场景走完整流程）：

```
avg_cost_per_conversation_usd : 0.001141
vs_case_budget ($0.15)        : 0.8%
routing                       : none 13 / large 2 / small 1
延迟                          : 模板 0s / glm-5-turbo 8.9s / glm-4.6 13.5s
experience_violations         : 0
```

13 次零调用（规则快路 + 确定性模板）才是成本压到预算 1% 以内的主因，不是选了便宜模型。

单会话另有 `CONVERSATION_COST_BUDGET_USD` 硬预算，超了强制降档到模板。

## 四、四层护栏

| 层 | 机制 | 演示 |
|---|---|---|
| **L1** 收窄动作空间 | LLM 只能 function call `propose_copy(offer_id ∈ 白名单)`；**结构里没有金额字段**，"退 200%" 在语法上不可表达 | — |
| **L2** Schema + 策略校验 | Pydantic `extra=forbid` + 白名单断言 + `value ≤ 订单 30%` | `force:"bad_offer"` → 422 `OFFER_NOT_IN_ALLOWLIST` |
| **L3** 出参文本扫描 | 正则扫金额/百分比/`keep the product`/`refund you`，未批准一律拦 | `force:"bad_text"` → `FORBIDDEN_PHRASE`；`force:"bad_amount"` → `UNAPPROVED_AMOUNT` |
| **L4** 执行层隔离 | 动钱只认 HMAC `offer_token`（TTL 15min + 幂等键），并**独立复核**上限 | 伪造 token → `BAD_SIGNATURE`；重放 → `already_executed` |

**prompt 里写「不要承诺退款」不是护栏。** 哪怕前三层全崩，L4 也让模型的任何一句话动不了一分钱。

## 五、体验不变量（要求 4）

写在 `config.EXPERIENCE_INVARIANTS`，由 `guardrails.assert_experience_invariants` 在每个响应返回前强制自检，违反即打点告警：

- 每一轮都必须带放弃挽留的出口（`escape_hatch`）
- 符合规则的退货绝不拒绝、绝不加阻力（美欧消费者保护红线）
- 婉拒必须附规则原文 + 至少一个替代方案
- 情绪超阈值场景禁止出现任何挽留 offer
- 最多 2 轮谈判，单会话最多 8 次交互

## 六、指标口径

`/api/metrics` **同时**上报两个挽留率口径，防止销售话术和 QBR 打架：

- `addressable_deflection_rate`（主口径，对应销售主张 80%）—— 分母已排除破损/错发/超窗口
- `overall_deflection_rate`（全量口径，预期 20–30%）

配套：强制 10% holdout 对照组（`RS_HOLDOUT_PCT=10`）做增量归因，**60 天**结算窗口，二次退货回冲。

商业口径、定价三档与异议应对见 [`docs/GTM-PRICING.md`](docs/GTM-PRICING.md)。

---

## 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| AI coding | Cursor + Claude Opus 5 | — |
| 后端 | FastAPI + Pydantic v2 | **护栏即 Schema** |
| LLM | 智谱 GLM 三档（flash / air / plus），OpenAI 兼容 | 换厂商只改环境变量 |
| 可观测 | Langfuse v4（OpenAI drop-in + scores） | 换个 import 就有全链路 trace |
| 数据 | 内存（默认）/ PostgreSQL 16 | 已实测双写落库 |
| 部署 | Railway / Fly.io | — |

## 文件结构

```
.env.example    配置模板（LLM / Langfuse / PostgreSQL / 策略阈值）
config.py       从 .env 读取全部配置 + summary() 供 /health
observability.py Langfuse 接入（traced OpenAI drop-in / span / score），未配置则 no-op
db.py           PostgreSQL 连接池与三张表的读写，memory 模式下全 no-op
db/schema.sql   建表脚本
models.py       枚举 + Pydantic Schema（L1 收窄动作空间）
store.py        mock 数据（7 客户 / 7 订单覆盖全场景）+ 会话 + 知识库
policy.py       规则核验 / 场景分类 / 分级退款 / 方案白名单
llm.py          规则快路 + 小模型意图 + 三档生成路由 + 成本核算
guardrails.py   L2/L3/L4 + 体验不变量断言
metrics.py      双口径挽留率 / 成本 / 路由分布 / 护栏触发
app.py          五阶段编排 + API
tests/test_e2e.py  26 个端到端测试
demo.sh         12 段演示脚本
```

## 已知边界（MVP 范围外）

- **`glm-4.6` 13.5s 延迟仍超出 12s 预算**，已打告警。下一步必须上流式输出，把首字延迟压到 1–2s
- `MODEL_PRICING` 是量级占位值；成本的**相对结构**是实测的，绝对值不是
- Postgres 已实测落库；但 `load_session` 尚未接回读路径，重启后仍从内存重建
- Langfuse 分数要查 `/api/public/v2/scores`（v1 端点返回空），且摄入有 ~15s 最终一致延迟
- 情绪识别靠词典 + 小模型，未做多语言（跨境场景需补西/法/德语词典）
- 人工工单队列是内存 list，未接 Zendesk/Gorgias
