"""全局配置。所有可调参数集中在这里，从 .env 读取，上线前按客户合同校准。

读取优先级：真实环境变量 > .env 文件 > 代码默认值。
（`load_dotenv` 默认不覆盖已存在的环境变量，容器里注入的 secret 永远赢。）
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _f(key: str, default: float) -> float:
    return float(os.getenv(key, default))


def _i(key: str, default: int) -> int:
    return int(os.getenv(key, default))


# ──────────────────────────────────────────── 运行环境
ENV = os.getenv("RS_ENV", "dev")
PORT = _i("RS_PORT", 8777)
SIGNING_KEY = os.getenv("RS_SIGNING_KEY", "dev-only-not-a-real-secret").encode()
if ENV == "prod" and SIGNING_KEY == b"dev-only-not-a-real-secret":
    raise RuntimeError("生产环境必须设置 RS_SIGNING_KEY（openssl rand -hex 32）")
DEFAULT_TENANT = os.getenv("RS_DEFAULT_TENANT", "public")

# ──────────────────────────────────────────── LLM 接入（OpenAI 兼容端点，默认智谱 GLM）
LLM_BASE_URL = (os.getenv("RS_LLM_BASE_URL") or os.getenv("GLM_BASE_URL")
                or "https://open.bigmodel.cn/api/paas/v4/")
LLM_API_KEY = os.getenv("RS_LLM_API_KEY") or os.getenv("GLM_API_KEY") or ""
USE_REAL_LLM = bool(LLM_API_KEY.strip())

# 三档模型：意图识别只用小模型，生成侧动态路由
# 模型名按 z.ai 端点实测可用集选定（见 docs/MODEL-SELECTION.md）
MODEL_INTENT = os.getenv("RS_MODEL_INTENT", "glm-4.5-air")   # 分类/情绪，关思考链
MODEL_SMALL = os.getenv("RS_MODEL_SMALL", "glm-5-turbo")     # 常规挽留话术
MODEL_LARGE = os.getenv("RS_MODEL_LARGE", "glm-4.6")         # 高情绪/高客单/僵持

# 思考链开关。GLM 新模型默认带 reasoning，对本产品的任务全是负收益：
#   意图分类   开思考 5.8x token，答案一样
#   生成话术   开思考 延迟翻倍(19-22s vs 9-10s)、输出 token 3x，而话术质量几乎无差别
# 挽留话术只有 2-3 句，共情 + 给方案，不是需要推理的任务。三档一律关。
# 注意 glm-5.3-flash* 强制思考、关不掉（错误码 1210），所以没选它们。
def _b(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


MODEL_THINKING: dict[str, bool] = {
    MODEL_INTENT: _b("RS_THINKING_INTENT", False),
    MODEL_SMALL: _b("RS_THINKING_SMALL", False),
    MODEL_LARGE: _b("RS_THINKING_LARGE", False),
}
# 聊天窗口里的延迟预算。超过这个数用户会直接关窗，比话术差一点严重得多。
LATENCY_BUDGET_SECONDS = _f("RS_LATENCY_BUDGET", 12.0)
# 推理模型会把 max_tokens 全烧在 reasoning 上导致 content 为空，必须留足预算
MODEL_MAX_TOKENS: dict[str, int] = {
    MODEL_INTENT: _i("RS_MAX_TOKENS_INTENT", 256),
    MODEL_SMALL: _i("RS_MAX_TOKENS_SMALL", 512),
    MODEL_LARGE: _i("RS_MAX_TOKENS_LARGE", 1024),
}

# 价格表 USD / 1K tokens。**量级占位值**，上线前必须按厂商合同价替换。
MODEL_PRICING: dict[str, dict[str, float]] = {
    MODEL_INTENT: {"in": 0.00020, "out": 0.00020},
    MODEL_SMALL:  {"in": 0.00060, "out": 0.00060},
    MODEL_LARGE:  {"in": 0.00600, "out": 0.00600},
}
CONVERSATION_COST_BUDGET_USD = _f("RS_COST_BUDGET", 0.05)

# ──────────────────────────────────────────── Langfuse 可观测性
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_BASE_URL = (os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST")
                     or "https://cloud.langfuse.com")
LANGFUSE_ENVIRONMENT = os.getenv("LANGFUSE_TRACING_ENVIRONMENT", ENV)
LANGFUSE_SAMPLE_RATE = _f("RS_LANGFUSE_SAMPLE_RATE", 1.0)
# 两把 key 都齐才开；缺任何一把就整体降级成 no-op，绝不因埋点挂掉主流程
USE_LANGFUSE = bool(LANGFUSE_PUBLIC_KEY.strip() and LANGFUSE_SECRET_KEY.strip())

# ──────────────────────────────────────────── PostgreSQL
STORE_BACKEND = os.getenv("RS_STORE_BACKEND", "memory")      # memory | postgres
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://returnsaver:returnsaver@localhost:5432/returnsaver")
DB_POOL_MIN = _i("RS_DB_POOL_MIN", 1)
DB_POOL_MAX = _i("RS_DB_POOL_MAX", 10)
DB_CONNECT_TIMEOUT = _i("RS_DB_CONNECT_TIMEOUT", 5)
USE_POSTGRES = STORE_BACKEND == "postgres"

# ──────────────────────────────────────────── 路由阈值
EMOTION_HARD_STOP = _f("RS_EMOTION_HARD_STOP", 0.70)    # ≥ 此值：禁止再挽留
EMOTION_LARGE_MODEL = _f("RS_EMOTION_LARGE_MODEL", 0.45)  # ≥ 此值：升大模型
HIGH_VALUE_ORDER_USD = _f("RS_HIGH_VALUE_ORDER", 200.0)   # ≥ 此值：升大模型
INTENT_RULE_CONFIDENCE = _f("RS_INTENT_RULE_CONFIDENCE", 0.85)  # 规则快路零成本阈值

# ──────────────────────────────────────────── 售后与挽留策略
MAX_NEGOTIATION_ROUNDS = _i("RS_MAX_NEGOTIATION_ROUNDS", 2)
MAX_SESSION_TURNS = _i("RS_MAX_SESSION_TURNS", 8)
MAX_DISCOUNT_PCT = _f("RS_MAX_DISCOUNT_PCT", 0.30)
INSTANT_REFUND_CAP_USD = _f("RS_INSTANT_REFUND_CAP", 50.0)
MANUAL_REVIEW_SLA_HOURS = _i("RS_MANUAL_SLA_HOURS", 2)
OFFER_TOKEN_TTL = _i("RS_OFFER_TOKEN_TTL", 900)
ABUSE_NEGOTIATION_LIMIT = _i("RS_ABUSE_NEGOTIATION_LIMIT", 3)

# ──────────────────────────────────────────── 实验
HOLDOUT_PCT = _i("RS_HOLDOUT_PCT", 0)   # 生产置 10：对照组做增量归因

# ──────────────────────────────────────────── 体验护栏（要求 4：降退货率不得损伤体验）
EXPERIENCE_INVARIANTS = {
    "always_offer_escape_hatch": True,   # 每一轮都给"还是要退"的出口
    "never_block_eligible_return": True, # 符合规则的退货绝不拒绝或加阻力
    "decline_must_cite_rule": True,      # 婉拒必须附规则原文 + 给替代方案
    "no_retention_when_angry": True,     # 情绪超阈值立刻停止挽留
    "max_rounds_enforced": True,
}

# ──────────────────────────────────────────── 商业口径（见 docs/GTM-PRICING.md）
# 对外主张的"退货率降低 80%"是 addressable 口径（已排除破损/错发/超窗口），
# 不是全量退货率。全量口径预期 20%~30%。埋点两个口径都报，防止销售与 QBR 打架。
TARGET_ADDRESSABLE_DEFLECTION = 0.80
TARGET_CSAT_LIFT = 0.30
TARGET_REVENUE_LIFT = 0.40


def summary() -> dict:
    """/health 用。绝不返回任何密钥本身，只报是否配置。"""
    return {
        "env": ENV,
        "llm": {"configured": USE_REAL_LLM, "base_url": LLM_BASE_URL,
                "intent": MODEL_INTENT, "small": MODEL_SMALL, "large": MODEL_LARGE},
        "langfuse": {"enabled": USE_LANGFUSE, "base_url": LANGFUSE_BASE_URL,
                     "environment": LANGFUSE_ENVIRONMENT,
                     "sample_rate": LANGFUSE_SAMPLE_RATE},
        "store": {"backend": STORE_BACKEND,
                  "db_host": DATABASE_URL.rsplit("@", 1)[-1] if USE_POSTGRES else None},
        "holdout_pct": HOLDOUT_PCT,
    }
