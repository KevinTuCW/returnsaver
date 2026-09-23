"""策略引擎：售后规则核验 + 场景分类 + 执行策略。
全部是确定性代码——LLM 在这里没有任何话语权，金额与动作只从这里产出。"""
from __future__ import annotations

import config as C
from models import Action, ReturnReason, Scenario
from store import KNOWLEDGE_BASE, MERCHANT_POLICY, ORDERS


# ──────────────────────────────────────────── S2 用户 / 订单校验
def verify_customer_and_orders(customer: dict | None, orders: list[dict]) -> tuple[bool, str]:
    if not customer:
        return False, "customer_not_found"
    if not orders:
        return False, "no_orders_found"
    return True, "ok"


def pick_candidate_orders(orders: list[dict]) -> list[dict]:
    """近 60 天内已签收的订单才是退货候选，按签收时间由近到远。"""
    cands = [o for o in orders if o["days_since_delivery"] <= 60]
    return sorted(cands, key=lambda o: o["days_since_delivery"])


# ──────────────────────────────────────────── S4 售后规则核验
def check_eligibility(order: dict, reason: ReturnReason) -> dict:
    """返回 {eligible, violated: [(code, text)], note}。
    破损/质量走豁免通道，不受窗口与拆封限制。"""
    p = MERCHANT_POLICY
    exempt = reason in (ReturnReason.DAMAGED, ReturnReason.QUALITY)
    violated: list[tuple[str, str]] = []

    if not exempt:
        if order["days_since_delivery"] > p["return_window_days"]:
            violated.append(("R-WINDOW", p["rules"]["R-WINDOW"]))
        if order["final_sale"]:
            violated.append(("R-FINAL", p["rules"]["R-FINAL"]))
        if order["category"] in ("innerwear", "swimwear") and order["opened"]:
            violated.append(("R-HYGIENE", p["rules"]["R-HYGIENE"]))
        # R-USED 的原文是"不支持**无理由**退货"，所以只约束 changed_mind：
        # 使用类问题必须先用过才会发现，尺码试穿也不算"明显使用"，拿这条挡人会踩体验红线
        if order["used"] and reason is ReturnReason.CHANGED_MIND:
            violated.append(("R-USED", p["rules"]["R-USED"]))

    return {
        "eligible": not violated,
        "violated": violated,
        "note": p["exceptions_note"] if exempt else None,
        "exempted": exempt,
    }


# ──────────────────────────────────────────── S5 场景分类（要求 3 的五类）
def classify_scenario(order: dict, reason: ReturnReason, emotion: float,
                      eligibility: dict) -> Scenario:
    # 情绪优先级最高，**高于合规性判定**：用户已经急了就别再分析原因了，直接进分级处理。
    # 这一条必须排在 eligible 之前——否则"不合规 + 已发火"会落进 NOT_ELIGIBLE，
    # allow_retention 保持 True，等于对着一个发火的用户继续推挽留方案。
    # 不合规订单不会被自动秒退：triage_refund 把不合规记成 anomaly，强制转人工。
    if emotion >= C.EMOTION_HARD_STOP:
        return Scenario.EMOTIONAL_INSIST
    if not eligibility["eligible"]:
        return Scenario.NOT_ELIGIBLE
    if reason in (ReturnReason.DAMAGED, ReturnReason.QUALITY):
        return Scenario.PRODUCT_DAMAGE
    if reason == ReturnReason.USAGE:
        return Scenario.USAGE_ISSUE
    return Scenario.VALUE_GAP     # 尺码/价格/不想要，统一走价值补偿线


# ──────────────────────────────────────────── 分级退款（要求 3 第五类）
def triage_refund(order: dict, customer: dict, eligibility: dict) -> tuple[Action, dict]:
    """情绪激烈执意要退：小额立即退，大额或异常人工介入。
    人工介入必须给明确时效承诺——这是体验钩子，不是拖延。"""
    anomalies = []
    if customer.get("risk_flag"):
        anomalies.append("customer_risk_flag")
    if customer.get("returns_last_90d", 0) >= 4:
        anomalies.append("high_return_frequency")
    if order["days_since_delivery"] > MERCHANT_POLICY["return_window_days"]:
        anomalies.append("outside_window")
    # 不合规订单绝不自动秒退：情绪高也只是"不再挽留"，钱该不该退是人来判
    if not eligibility["eligible"]:
        anomalies.append("not_eligible")

    if order["total"] <= C.INSTANT_REFUND_CAP_USD and not anomalies:
        return Action.INSTANT_REFUND, {
            "refund_amount": order["total"],
            "eta": "即时到账，银行入账 1-3 个工作日",
            "keep_item": order["total"] <= 20,   # 极小额免退回，逆向物流不划算
        }
    return Action.ESCALATE_HUMAN, {
        "sla_hours": C.MANUAL_REVIEW_SLA_HOURS,
        "anomalies": anomalies,
        "reason": "high_value" if order["total"] > C.INSTANT_REFUND_CAP_USD else "anomaly",
        "priority": "P1" if order["total"] > 500 else "P2",
    }


# ──────────────────────────────────────────── 各场景的可选方案（白名单，LLM 只能从中挑）
def build_resolution(order: dict, customer: dict, scenario: Scenario,
                     eligibility: dict, round_no: int) -> dict:
    """返回 {action, offers[], payload, allow_retention}。
    offers 是 LLM 唯一能引用的动作集合；allow_retention=False 时禁止任何挽留话术。"""
    total = order["total"]
    cap = round(total * C.MAX_DISCOUNT_PCT, 2)
    kb = KNOWLEDGE_BASE.get(order["sku"])

    if scenario is Scenario.NOT_ELIGIBLE:
        # 婉拒也要给台阶：附规则原文 + 至少一个替代方案，不能一句"不行"了事
        offers = [{"offer_id": "GOODWILL_COUPON", "type": "coupon",
                   "value": min(round(total * 0.10, 2), cap),
                   "label": f"虽然这单不符合退货规则，送你 ${min(round(total*0.10,2), cap):.2f} 下单可用优惠券"}]
        if order["repairable"]:
            offers.insert(0, {"offer_id": "PAID_REPAIR", "type": "repair", "value": 0.0,
                              "label": "可安排官方维修，检测免费，配件费另计"})
        return {"action": Action.DECLINE_WITH_RULES, "offers": offers,
                "payload": {"violated": eligibility["violated"],
                            "policy_note": MERCHANT_POLICY["exceptions_note"]},
                "allow_retention": True}

    if scenario is Scenario.USAGE_ISSUE:
        offers = [{"offer_id": "SEND_GUIDE", "type": "guide", "value": 0.0,
                   "label": "发送图文手册 + 上手技巧，并把退货窗口延长 14 天"}]
        if kb and kb.get("video_url"):
            offers.insert(0, {"offer_id": "SEND_VIDEO_GUIDE", "type": "guide", "value": 0.0,
                              "label": "发送 90 秒上手视频 + 三条调校技巧，退货窗口延长 14 天"})
        offers.append({"offer_id": "LIVE_ONBOARDING", "type": "service", "value": 0.0,
                       "label": "预约 10 分钟 1v1 视频指导，免费"})
        return {"action": Action.SEND_GUIDE, "offers": offers,
                "payload": {"kb": kb}, "allow_retention": True}

    if scenario is Scenario.PRODUCT_DAMAGE:
        offers = []
        if order["repairable"]:
            offers.append({"offer_id": "FREE_REPAIR", "type": "repair", "value": 0.0,
                           "label": "免费上门取件维修，往返运费我们承担"})
        offers.append({"offer_id": "FREE_EXCHANGE", "type": "exchange", "value": 0.0,
                       "label": "免费换新，先发新品后回收旧件"})
        offers.append({"offer_id": "PARTIAL_KEEP", "type": "store_credit",
                       "value": min(round(total * 0.25, 2), cap),
                       "label": f"不介意瑕疵的话，保留商品并退 ${min(round(total*0.25,2), cap):.2f}"})
        return {"action": Action.OFFER_REPAIR_EXCHANGE, "offers": offers,
                "payload": {"note": "破损件全程免运费，且不占用退货次数"},
                "allow_retention": True}

    if scenario is Scenario.VALUE_GAP:
        if customer.get("risk_flag") or order["negotiations_last_90d"] >= C.ABUSE_NEGOTIATION_LIMIT:
            return {"action": Action.STANDARD_RETURN, "offers": [],
                    "payload": {"reason": "abuse_cooldown"}, "allow_retention": False}
        tier = 0.25 if round_no >= 2 else 0.15     # 阶梯让利，第二轮才加码
        credit = min(round(total * tier, 2), cap)
        offers = []
        if order["category"] in ("apparel", "footwear") and order["sizes_in_stock"]:
            offers.append({"offer_id": "FREE_EXCHANGE", "type": "exchange", "value": 0.0,
                           "label": f"免费换码（现货 {'/'.join(order['sizes_in_stock'])}），双程运费我们出"})
        offers.append({"offer_id": f"CREDIT_{int(tier*100)}", "type": "store_credit",
                       "value": credit, "label": f"保留商品，赠 ${credit:.2f} 店铺 credit"})
        offers.append({"offer_id": f"REFUND_PARTIAL_{int(tier*100)}", "type": "partial_refund",
                       "value": credit, "label": f"保留商品，原路退 ${credit:.2f}"})
        return {"action": Action.OFFER_COMPENSATION, "offers": offers,
                "payload": {"tier_pct": tier}, "allow_retention": True}

    # EMOTIONAL_INSIST：分级处理，且**禁止任何挽留话术**
    action, payload = triage_refund(order, customer, eligibility)
    if not eligibility["eligible"]:
        # 转人工时把违反的规则一起带过去，人工才知道为什么不能直接退
        payload["violated"] = eligibility["violated"]
    return {"action": action, "offers": [], "payload": payload, "allow_retention": False}
