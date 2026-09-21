"""全局配置与阈值。所有可调参数集中在这里，上线前按客户合同校准。"""
from __future__ import annotations

import os

ENV = os.getenv("RS_ENV", "dev")
SIGNING_KEY = os.getenv("RS_SIGNING_KEY", "dev-only-not-a-real-secret").encode()

# ──────────────────────────────────────────── LLM 接入（OpenAI 兼容端点，默认智谱 GLM）
LLM_BASE_URL = (os.getenv("RS_LLM_BASE_URL") or os.getenv("GLM_BASE_URL")
                or "https://open.bigmodel.cn/api/paas/v4/")
LLM_API_KEY = os.getenv("RS_LLM_API_KEY") or os.getenv("GLM_API_KEY")
USE_REAL_LLM = bool(LLM_API_KEY)

# 三档模型：意图识别只用小模型，生成侧动态路由
MODEL_INTENT = os.getenv("RS_MODEL_INTENT", "glm-4-flash")   # 分类/情绪
MODEL_SMALL = os.getenv("RS_MODEL_SMALL", "glm-4-air")       # 常规挽留话术
MODEL_LARGE = os.getenv("RS_MODEL_LARGE", "glm-4-plus")      # 高情绪/高客单/僵持

# 价格表 USD / 1K tokens。**量级占位值**，上线前必须按厂商合同价替换。
MODEL_PRICING: dict[str, dict[str, float]] = {
    MODEL_INTENT: {"in": 0.00007, "out": 0.00007},
    MODEL_SMALL:  {"in": 0.00070, "out": 0.00070},
    MODEL_LARGE:  {"in": 0.00700, "out": 0.00700},
}
# 成本预算护栏：单次会话 LLM 成本超过这个数就强制降档到模板
CONVERSATION_COST_BUDGET_USD = float(os.getenv("RS_COST_BUDGET", "0.05"))

# ──────────────────────────────────────────── 路由阈值
EMOTION_HARD_STOP = 0.70      # ≥ 此值：禁止再挽留，直接进入执行/升级
EMOTION_LARGE_MODEL = 0.45    # ≥ 此值：生成侧升到大模型
HIGH_VALUE_ORDER_USD = 200.0  # ≥ 此值：生成侧升到大模型（挽回价值高，值得多花钱）
INTENT_RULE_CONFIDENCE = 0.85 # 规则快路置信度，达标则不调用任何模型（零成本）

# ──────────────────────────────────────────── 售后与挽留策略
MAX_NEGOTIATION_ROUNDS = 2          # 最多谈 2 轮，之后无条件放行
MAX_SESSION_TURNS = 8               # 单会话总交互上限，防打转
MAX_DISCOUNT_PCT = 0.30             # 任何让利 ≤ 订单金额 30%
INSTANT_REFUND_CAP_USD = 50.0       # 情绪激烈 + 小额：立即退，不走审核
MANUAL_REVIEW_SLA_HOURS = 2         # 大额/异常转人工的响应时效承诺
OFFER_TOKEN_TTL = 900               # offer 凭证 15 分钟
ABUSE_NEGOTIATION_LIMIT = 3         # 90 天内谈判次数上限，超过不再给券

# ──────────────────────────────────────────── 体验护栏（要求 4：降退货率不得损伤体验）
# 这些是硬约束，任何挽留策略都不能绕过
EXPERIENCE_INVARIANTS = {
    "always_offer_escape_hatch": True,   # 每一轮都给"还是要退"的出口
    "never_block_eligible_return": True, # 符合规则的退货绝不拒绝或加阻力
    "decline_must_cite_rule": True,      # 婉拒必须附规则原文 + 给替代方案
    "no_retention_when_angry": True,     # 情绪超阈值立刻停止挽留
    "max_rounds_enforced": True,
}

# ──────────────────────────────────────────── 商业口径（见 docs/GTM-PRICING.md）
# 注意：对外主张的"退货率降低 80%"口径 = 可挽回退货(addressable)中的挽留率，
# 不是全量退货率。全量口径的预期是 20%~30%。埋点按 addressable 口径计算。
TARGET_ADDRESSABLE_DEFLECTION = 0.80
TARGET_CSAT_LIFT = 0.30
TARGET_REVENUE_LIFT = 0.40
