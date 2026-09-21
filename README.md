# Return Saver — 退货挽留 AI Agent（MVP）

面向跨境 DTC Shopify 品牌。chat widget 检测到退货意图后调用本服务，在**不损伤用户体验**的前提下尽量把这单救回来。

**两条不可违背的主张**
1. **LLM 有建议权，没有执行权** —— 金额与动作只从确定性策略引擎出，四层护栏兜底。
2. **降退货率不得以伤体验换** —— 体验约束写成会抛错的断言，不是 prompt 里的建议。

---

## 跑起来

```bash
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn pydantic openai pytest httpx
.venv/bin/python -m uvicorn app:app --port 8777
bash demo.sh                       # 12 段全流程演示
.venv/bin/python -m pytest tests -q # 26 个 e2e 测试
```

**无需 API key 即可完整演示**：没 key 时自动降级到确定性 mock，断网/限流也不翻车。
接真 LLM（默认智谱 GLM，OpenAI 兼容端点）：

```bash
export GLM_API_KEY=<your key>
# 换厂商：export RS_LLM_BASE_URL=... RS_MODEL_INTENT=... RS_MODEL_SMALL=... RS_MODEL_LARGE=...
```

> 本机若开着代理，`curl` 需加 `--noproxy '*'`，否则 localhost 会被代理劫持。

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

**实测**（`/api/metrics`，8 次会话）：

```
avg_cost_per_conversation_usd : 0.001383
vs_case_budget ($0.15)        : 0.9%
routing                       : none 16 / large 3 / small 1
experience_violations         : 0
```

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
| 数据 | MVP 内存 mock；上线 Supabase(Postgres) | `store.py` 接口签名不变 |
| 部署 | Railway / Fly.io | — |

## 文件结构

```
config.py       阈值与开关（模型、价格表、体验不变量、商业口径）
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

- 会话状态在内存，重启即丢；生产需 Redis/Postgres
- `MODEL_PRICING` 是量级占位值，上线前必须按厂商合同价替换
- **真 LLM 路径尚未用真实 key 验证过**，当前所有数据来自确定性 mock 路径
- 情绪识别靠词典 + 小模型，未做多语言（跨境场景需补西/法/德语词典）
- 人工工单队列是内存 list，未接 Zendesk/Gorgias
