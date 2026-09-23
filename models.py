"""枚举与 Pydantic Schema。Schema 即护栏——LLM 能产出什么，这里就框死什么。"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ──────────────────────────────────────────── 流程状态机（要求 2 的五个阶段）
class Stage(str, Enum):
    INTENT = "S1_intent"            # 用户意图识别
    VERIFY = "S2_verify"            # 用户信息 / 订单信息校验
    CONFIRM = "S3_confirm"          # 和用户确认订单
    ELIGIBILITY = "S4_eligibility"  # 订单 / 商品售后规则核验
    EXECUTE = "S5_execute"          # 按场景执行策略
    CLOSED = "S6_closed"


class Intent(str, Enum):
    RETURN = "return_request"       # 明确要退货
    COMPLAINT = "complaint"         # 抱怨但未必要退
    QUESTION = "question"           # 使用咨询
    OTHER = "other"                 # 与售后无关，交还主客服


# ──────────────────────────────────────────── 场景分类（要求 3 的五类）
class Scenario(str, Enum):
    NOT_ELIGIBLE = "not_eligible"          # 不满足售后规则 → 附规则婉拒
    USAGE_ISSUE = "usage_issue"            # 满足 + 使用问题 → 手册/培训
    VALUE_GAP = "value_gap"                # 满足 + 价格价值不符 → 补偿/优惠券
    PRODUCT_DAMAGE = "product_damage"      # 满足 + 产品损坏 → 修理/换货
    EMOTIONAL_INSIST = "emotional_insist"  # 满足 + 情绪激烈执意退 → 分级处理


class Action(str, Enum):
    DECLINE_WITH_RULES = "decline_with_rules"
    SEND_GUIDE = "send_guide"
    OFFER_COMPENSATION = "offer_compensation"
    OFFER_REPAIR_EXCHANGE = "offer_repair_exchange"
    INSTANT_REFUND = "instant_refund"        # 小额立即退
    ESCALATE_HUMAN = "escalate_human"        # 大额/异常人工介入
    STANDARD_RETURN = "standard_return"      # 放行标准退货


class ModelTier(str, Enum):
    NONE = "none"    # 纯模板，零 LLM 成本
    SMALL = "small"
    LARGE = "large"


class ReturnReason(str, Enum):
    SIZE_FIT = "size_fit"
    DAMAGED = "damaged"
    QUALITY = "quality"
    VALUE_GAP = "value_gap"
    USAGE = "usage"
    CHANGED_MIND = "changed_mind"
    UNKNOWN = "unknown"


# ──────────────────────────────────────────── LLM 可产出的唯一结构（L1 收窄动作空间）
class IntentResult(BaseModel):
    """小模型的分类输出。只有标签和分数，没有任何自由决策。"""
    model_config = {"extra": "forbid"}
    intent: Intent
    reason: ReturnReason
    emotion: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class CopyProposal(BaseModel):
    """生成模型的唯一出口：选一个白名单 offer + 写一段话术。
    刻意不设 amount 字段——金额在语法上就不可表达。"""
    model_config = {"extra": "forbid"}
    offer_id: str = Field(min_length=1, max_length=48)
    message: str = Field(min_length=1, max_length=600)


# ──────────────────────────────────────────── API 契约
class NegotiateRequest(BaseModel):
    model_config = {"extra": "forbid"}
    session_id: str | None = None
    customer_id: str | None = None
    message: str = Field(min_length=1, max_length=2000)
    confirm_order_id: str | None = None          # S3 用户确认订单
    want_return_anyway: bool = False             # 体验出口：随时可放弃挽留
    force: Literal["bad_offer", "bad_text", "bad_amount"] | None = None  # 仅演示护栏


class AcceptRequest(BaseModel):
    model_config = {"extra": "forbid"}
    offer_token: str
    idempotency_key: str = Field(min_length=4, max_length=64)


class Offer(BaseModel):
    offer_id: str
    type: str
    value: float = Field(ge=0.0)
    label: str
