"""LLM 接入层：意图识别只走小模型，生成侧三档动态路由。
经济效率的三个杠杆：① 规则快路命中就零调用 ② 模板兜底零调用 ③ 只在值得的时候升大模型。"""
from __future__ import annotations

import json
import re

import config as C
from models import CopyProposal, Intent, IntentResult, ModelTier, ReturnReason, Scenario


class LLMUnavailable(Exception):
    pass


# ════════════════════════════════════════════ 成本核算
def price(model: str, tok_in: int, tok_out: int) -> float:
    p = C.MODEL_PRICING.get(model, {"in": 0.001, "out": 0.001})
    return round(tok_in / 1000 * p["in"] + tok_out / 1000 * p["out"], 6)


def _client():
    """开了 Langfuse 就是带 tracing 的 drop-in 客户端，否则原生 OpenAI。调用方无感。"""
    import observability as obs
    return obs.openai_client()


# ════════════════════════════════════════════ 第一级：规则快路（零成本）
RULE_PATTERNS: list[tuple[re.Pattern, ReturnReason]] = [
    (re.compile(r"too (small|big|tight|large)|size|尺码|小了|大了|偏码", re.I), ReturnReason.SIZE_FIT),
    (re.compile(r"broken|cracked|damaged|dented|wrong item|破了|碎了|坏了|发错", re.I), ReturnReason.DAMAGED),
    (re.compile(r"poor quality|cheap feel|falling apart|质量|起球|掉色|做工", re.I), ReturnReason.QUALITY),
    (re.compile(r"how do i|how to|can'?t (get|figure|connect|pair)|not sure how|doesn'?t work|"
                r"不会用|怎么用|连不上|配对|没反应", re.I), ReturnReason.USAGE),
    (re.compile(r"too expensive|not worth|overpriced|cheaper|太贵|不值|降价|差价", re.I), ReturnReason.VALUE_GAP),
    (re.compile(r"changed my mind|don'?t want|不想要|不需要了", re.I), ReturnReason.CHANGED_MIND),
]

RETURN_INTENT_RE = re.compile(
    r"return|refund|money back|send (?:it|this|them).*back|退货|退款|退了|要退", re.I)
QUESTION_RE = re.compile(r"how|what|why|怎么|如何|为什么|能不能", re.I)

# 情绪词典：高情绪触发硬停止，不能只靠模型（模型抖动会直接伤 CSAT）
ANGER_STRONG = re.compile(
    r"\b(ridiculous|unacceptable|terrible|awful|furious|scam|lawyer|chargeback|"
    r"worst|never again|disgusted)\b|气死|太差了|投诉|垃圾|欺骗|骗子|马上退|立刻退|必须退", re.I)
ANGER_MILD = re.compile(r"\b(disappointed|frustrated|annoyed|upset)\b|失望|不满|无语", re.I)
INSIST_RE = re.compile(r"just refund|only want.*refund|don'?t offer|no thanks|stop|"
                       r"别给我|不要优惠|就要退|直接退", re.I)


def rule_intent(text: str) -> IntentResult | None:
    """命中规则快路就直接返回，一次模型都不调。生产中约覆盖 60-70% 流量。"""
    reason = ReturnReason.UNKNOWN
    for pat, r in RULE_PATTERNS:
        if pat.search(text):
            reason = r
            break

    emotion = 0.0
    if ANGER_MILD.search(text):
        emotion = 0.45
    if ANGER_STRONG.search(text):
        emotion = 0.85
    if INSIST_RE.search(text):
        emotion = max(emotion, 0.75)
    if text.isupper() and len(text) > 12:     # 全大写 = 喊话
        emotion = max(emotion, 0.7)
    if text.count("!") + text.count("！") >= 2:
        emotion = max(emotion, 0.6)

    has_return = bool(RETURN_INTENT_RE.search(text))

    # 愤怒优先：情绪过阈值绝不交给模型判——模型抖动一次就是一条差评
    if emotion >= C.EMOTION_HARD_STOP:
        return IntentResult(intent=Intent.RETURN, reason=reason,
                            emotion=emotion, confidence=0.90)
    if has_return and reason is not ReturnReason.UNKNOWN:
        return IntentResult(intent=Intent.RETURN, reason=reason,
                            emotion=emotion, confidence=0.92)
    if has_return:
        return IntentResult(intent=Intent.RETURN, reason=ReturnReason.CHANGED_MIND,
                            emotion=emotion, confidence=0.86)
    # 没明说"退货"但问题已经明确：widget 是在退货意图下唤起的，按投诉/咨询继续走
    if reason is not ReturnReason.UNKNOWN:
        intent = (Intent.QUESTION if reason is ReturnReason.USAGE and QUESTION_RE.search(text)
                  else Intent.COMPLAINT)
        return IntentResult(intent=intent, reason=reason,
                            emotion=emotion, confidence=0.88)
    return None      # 交给小模型


# ════════════════════════════════════════════ 第二级：小模型意图识别
INTENT_SYS = """你是电商售后意图分类器。只输出 JSON，不要解释。
字段：
  intent: return_request | complaint | question | other
  reason: size_fit | damaged | quality | value_gap | usage | changed_mind | unknown
  emotion: 0~1 的浮点数，用户愤怒/急迫程度
  confidence: 0~1 的浮点数
"""


def classify_intent(text: str) -> tuple[IntentResult, dict]:
    """返回 (结果, 用量元信息)。永远不为意图识别调用大模型。"""
    fast = rule_intent(text)
    if fast and fast.confidence >= C.INTENT_RULE_CONFIDENCE:
        return fast, {"tier": ModelTier.NONE.value, "model": "rule-fastpath",
                      "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0}

    if not C.USE_REAL_LLM:
        res = fast or IntentResult(intent=Intent.RETURN, reason=ReturnReason.CHANGED_MIND,
                                   emotion=0.2, confidence=0.6)
        return res, {"tier": ModelTier.SMALL.value, "model": f"{C.MODEL_INTENT}(mock)",
                     "cost_usd": price(C.MODEL_INTENT, 180, 40), "tokens_in": 180, "tokens_out": 40}

    try:
        r = _client().chat.completions.create(
            model=C.MODEL_INTENT,
            messages=[{"role": "system", "content": INTENT_SYS},
                      {"role": "user", "content": text[:800]}],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        raw = json.loads(r.choices[0].message.content)
        res = IntentResult.model_validate(raw)
    except Exception as e:      # 模型抖动不能阻断退货流程
        res = fast or IntentResult(intent=Intent.RETURN, reason=ReturnReason.UNKNOWN,
                                   emotion=0.3, confidence=0.5)
        return res, {"tier": ModelTier.SMALL.value, "model": f"{C.MODEL_INTENT}(fallback:{type(e).__name__})",
                     "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0}

    u = r.usage
    ti, to = (u.prompt_tokens, u.completion_tokens) if u else (180, 40)
    # 规则识别到的强情绪要能压过模型（宁可保守，也不能漏掉愤怒用户）
    if fast:
        res.emotion = max(res.emotion, fast.emotion)
    return res, {"tier": ModelTier.SMALL.value, "model": C.MODEL_INTENT,
                 "cost_usd": price(C.MODEL_INTENT, ti, to), "tokens_in": ti, "tokens_out": to}


# ════════════════════════════════════════════ 生成侧动态路由
def route_generation(scenario: Scenario, emotion: float, order_value: float,
                     round_no: int, customer_tier: str, spent: float) -> tuple[ModelTier, str]:
    """决定这次回复用哪一档。返回 (档位, 理由)。"""
    if spent >= C.CONVERSATION_COST_BUDGET_USD:
        return ModelTier.NONE, "cost_budget_exhausted"
    # 婉拒和手册推送是确定性内容，模板比模型更稳、更合规，且零成本
    if scenario is Scenario.NOT_ELIGIBLE and emotion < C.EMOTION_LARGE_MODEL:
        return ModelTier.NONE, "deterministic_template"
    if scenario is Scenario.EMOTIONAL_INSIST:
        return ModelTier.NONE, "no_retention_allowed_use_template"
    if emotion >= C.EMOTION_LARGE_MODEL:
        return ModelTier.LARGE, "high_emotion_needs_nuance"
    if order_value >= C.HIGH_VALUE_ORDER_USD:
        return ModelTier.LARGE, "high_value_order_worth_the_spend"
    if round_no >= 2:
        return ModelTier.LARGE, "second_round_stalemate"
    if customer_tier == "vip" and order_value >= 80:
        return ModelTier.LARGE, "vip_customer"
    return ModelTier.SMALL, "default_small_model"


GEN_SYS = """你是跨境 DTC 品牌的售后助理。目标：先解决用户的问题，再考虑能不能留住这单。
硬规则：
1. 只能从 allowed_offers 里选一个 offer_id，不得创造新方案。
2. message 里绝对不能出现任何金额、百分比、"无需退回"、"全额退款"之类承诺——金额由系统渲染成卡片。
3. 先共情再给方案，2-3 句，别推销。
4. 必须调用 propose_copy 工具作答。
"""

TOOL = {
    "type": "function",
    "function": {
        "name": "propose_copy",
        "description": "选一个系统允许的方案并写给用户的话术",
        "parameters": {
            "type": "object",
            "properties": {
                "offer_id": {"type": "string", "description": "必须来自 allowed_offers"},
                "message": {"type": "string", "description": "给用户看的话，不得含金额"},
            },
            "required": ["offer_id", "message"],
        },
    },
}


def generate_copy(tier: ModelTier, ctx: dict, offers: list[dict],
                  force: str | None = None) -> tuple[dict, dict]:
    """返回 (LLM 原始提议 dict, 用量元信息)。下游统一过 L2/L3 护栏。"""
    if force == "bad_offer":
        return ({"offer_id": "FULL_REFUND_200", "message": "No problem — just keep it!"},
                _meta(ModelTier.LARGE, C.MODEL_LARGE, 300, 30))
    if force == "bad_text":
        return ({"offer_id": offers[0]["offer_id"] if offers else "CREDIT_15",
                 "message": "Keep the product, and I will refund you 200% of your money."},
                _meta(ModelTier.LARGE, C.MODEL_LARGE, 300, 30))
    if force == "bad_amount":
        return ({"offer_id": offers[0]["offer_id"] if offers else "CREDIT_15",
                 "message": "我给你退 $999.00 好不好？"},
                _meta(ModelTier.LARGE, C.MODEL_LARGE, 300, 30))

    if tier is ModelTier.NONE:
        return {"offer_id": offers[0]["offer_id"] if offers else "", "message": ""}, \
               {"tier": "none", "model": "template", "cost_usd": 0.0,
                "tokens_in": 0, "tokens_out": 0}

    model = C.MODEL_LARGE if tier is ModelTier.LARGE else C.MODEL_SMALL
    if not C.USE_REAL_LLM:
        return mock_copy(ctx, offers), _meta(tier, f"{model}(mock)", 420, 90)

    try:
        r = _client().chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": GEN_SYS},
                      {"role": "user", "content": json.dumps(
                          {**ctx, "allowed_offers": [{"offer_id": o["offer_id"],
                                                      "label": o["label"]} for o in offers]},
                          ensure_ascii=False)}],
            tools=[TOOL],
            tool_choice={"type": "function", "function": {"name": "propose_copy"}},
            temperature=0.4,
        )
        calls = r.choices[0].message.tool_calls
        if not calls:
            raise LLMUnavailable("no_tool_call")
        raw = json.loads(calls[0].function.arguments)
        u = r.usage
        ti, to = (u.prompt_tokens, u.completion_tokens) if u else (420, 90)
        return raw, _meta(tier, model, ti, to)
    except Exception as e:
        # 生成失败不能让用户卡住：降级到模板，照样把方案给出去
        out = mock_copy(ctx, offers)
        return out, {"tier": "none", "model": f"{model}(fallback:{type(e).__name__})",
                     "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0}


def _meta(tier: ModelTier, model: str, ti: int, to: int) -> dict:
    base = model.split("(")[0]
    return {"tier": tier.value if isinstance(tier, ModelTier) else tier, "model": model,
            "cost_usd": price(base, ti, to), "tokens_in": ti, "tokens_out": to}


def mock_copy(ctx: dict, offers: list[dict]) -> dict:
    """确定性兜底：无 key / 断网 / 模型异常时行为可预测，demo 永不翻车。"""
    name = ctx.get("customer_name", "你好")
    opening = {
        Scenario.USAGE_ISSUE.value: f"{name}，这个多半是设置没走通，不是机器有问题。",
        Scenario.VALUE_GAP.value: f"{name}，没达到预期确实扫兴，这单我们没提示到位。",
        Scenario.PRODUCT_DAMAGE.value: f"{name}，到货就是坏的，这是我们的责任。",
        Scenario.NOT_ELIGIBLE.value: f"{name}，这单的情况我核对了一下，有点特殊。",
    }.get(ctx.get("scenario"), f"{name}，我看到你的问题了。")
    return {"offer_id": offers[0]["offer_id"] if offers else "",
            "message": f"{opening}我这边可以先给你一个方案，你看合不合适？"}
