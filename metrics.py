"""指标埋点。口径见 config.py：对外主张的"退货率降低 80%"是 addressable 口径。

addressable（可挽回）= 排除破损/错发/超窗口这些本来就不该挽留的请求。
全量 deflection 与 addressable deflection 同时上报，避免销售口径和 QBR 口径打架。
"""
from __future__ import annotations

from collections import Counter, defaultdict

import config as C
from models import Action, Scenario

# 非可挽回场景：这些本就不该被计入挽留分母
NON_ADDRESSABLE = {Scenario.PRODUCT_DAMAGE.value, Scenario.NOT_ELIGIBLE.value}

_M = {
    "conversations": 0,
    "by_scenario": Counter(),
    "by_action": Counter(),
    "by_model_tier": Counter(),
    "llm_cost_usd": 0.0,
    "deflected": 0,            # 全量口径分子
    "addressable_total": 0,
    "addressable_deflected": 0,
    "escalations": 0,
    "instant_refunds": 0,
    "guardrail_trips": Counter(),
    "experience_violations": 0,
    "csat_samples": [],
    "holdout_conversations": 0,   # 10% 对照组，用于增量归因
}

DEFLECTING_ACTIONS = {Action.SEND_GUIDE.value, Action.OFFER_COMPENSATION.value,
                      Action.OFFER_REPAIR_EXCHANGE.value, Action.DECLINE_WITH_RULES.value}

# session_id → 上一次为这段会话记的账。一段会话可能被记多次（两轮挽留、最后放行），
# 但看板上它只能算**一段**会话，口径必须是"最终结局"。
_SEEN: dict[str, dict] = {}


def _apply(s: dict, sign: int = 1) -> None:
    """把一次记账加到（sign=1）或从（sign=-1）看板上撤下来。"""
    _M["by_scenario"][s["scenario"]] += sign
    _M["by_action"][s["action"]] += sign
    # cost 传的是会话**累计**成本：撤旧值 + 加新值，净效果正好是增量
    _M["llm_cost_usd"] = round(_M["llm_cost_usd"] + sign * s["cost"], 6)
    for t in s["tiers"]:
        _M["by_model_tier"][t] += sign
    if s["scenario"] not in NON_ADDRESSABLE:
        _M["addressable_total"] += sign
        if s["action"] in DEFLECTING_ACTIONS:
            _M["addressable_deflected"] += sign
    if s["action"] in DEFLECTING_ACTIONS:
        _M["deflected"] += sign
    if s["action"] == Action.ESCALATE_HUMAN.value:
        _M["escalations"] += sign
    if s["action"] == Action.INSTANT_REFUND.value:
        _M["instant_refunds"] += sign


def record_conversation(session_id: str, scenario: str, action: str, cost: float,
                        tiers: list[str], holdout: bool = False) -> None:
    """按会话记账，可重复调用。同一个 session_id 再来一次 = 改写它的结局，
    而不是多算一段会话——否则两轮挽留会让 conversations 翻倍、成本被重复累加。"""
    prev = _SEEN.get(session_id)
    if prev is None:
        _M["conversations"] += 1
        if holdout:
            _M["holdout_conversations"] += 1
    else:
        _apply(prev, -1)
    snap = {"scenario": scenario, "action": action, "cost": cost, "tiers": list(tiers)}
    _apply(snap, 1)
    _SEEN[session_id] = snap


def record_guardrail(layer: str, code: str) -> None:
    _M["guardrail_trips"][f"{layer}:{code}"] += 1
    if layer == "EXP":
        _M["experience_violations"] += 1


def record_csat(score: int) -> None:
    _M["csat_samples"].append(score)


def _nonzero(c: Counter) -> dict:
    return {k: v for k, v in c.items() if v}


def snapshot() -> dict:
    conv = max(_M["conversations"], 1)
    addr = max(_M["addressable_total"], 1)
    csat = _M["csat_samples"]
    return {
        "conversations": _M["conversations"],
        "cost": {
            "llm_cost_usd_total": round(_M["llm_cost_usd"], 6),
            "avg_cost_per_conversation_usd": round(_M["llm_cost_usd"] / conv, 6),
            "case_budget_usd": 0.15,
            "vs_case_budget": f"{round(_M['llm_cost_usd'] / conv / 0.15 * 100, 1)}%",
            "cost_per_deflection_usd": round(
                _M["llm_cost_usd"] / max(_M["deflected"], 1), 6),
        },
        "north_star": {
            # 主口径：可挽回退货中的挽留率，对应销售主张的 80%
            "addressable_deflection_rate": round(_M["addressable_deflected"] / addr, 4),
            "target": C.TARGET_ADDRESSABLE_DEFLECTION,
            # 副口径：全量，防止销售话术和 QBR 打架
            "overall_deflection_rate": round(_M["deflected"] / conv, 4),
        },
        "experience": {
            "escalations": _M["escalations"],
            "escalation_sla_hours": C.MANUAL_REVIEW_SLA_HOURS,
            "instant_refunds": _M["instant_refunds"],
            "avg_csat": round(sum(csat) / len(csat), 2) if csat else None,
            "experience_violations": _M["experience_violations"],
        },
        # 改写结局会把旧分类减回 0，这些空桶不该出现在看板上
        "routing": _nonzero(_M["by_model_tier"]),
        "scenarios": _nonzero(_M["by_scenario"]),
        "actions": _nonzero(_M["by_action"]),
        "guardrail_trips": _nonzero(_M["guardrail_trips"]),
        "holdout_conversations": _M["holdout_conversations"],
        "caveat": "addressable 口径已排除破损/错发/超窗口；增量效果须与 10% holdout 对照组比较",
    }


def reset() -> None:
    _SEEN.clear()
    for k, v in list(_M.items()):
        if isinstance(v, Counter):
            v.clear()
        elif isinstance(v, list):
            v.clear()
        elif isinstance(v, float):
            _M[k] = 0.0
        else:
            _M[k] = 0
