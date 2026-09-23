"""端到端测试：五阶段流程 / 五个场景 / 四层护栏 / 路由经济性 / 体验不变量。
运行：.venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as C          # noqa: E402
import metrics              # noqa: E402
from app import app         # noqa: E402

client = TestClient(app)


def neg(**kw):
    r = client.post("/api/negotiate", json=kw)
    return r.status_code, r.json()


def full(customer_id: str, order_id: str, message: str, **extra):
    """跑完 S1→S3：先拿到确认请求，再带 confirm_order_id 推进到 S5。"""
    _, first = neg(customer_id=customer_id, message=message)
    assert first["status"] == "awaiting_order_confirmation", first
    return neg(session_id=first["session_id"], customer_id=customer_id,
               message=message, confirm_order_id=order_id, **extra)


@pytest.fixture(autouse=True)
def _reset():
    metrics.reset()
    yield


# ════════════════════════════════ 五阶段流程
def test_s3_requires_explicit_order_confirmation():
    """S3：绝不能跳过确认直接动订单。"""
    _, r = neg(customer_id="C-001", message="I want to return this")
    assert r["status"] == "awaiting_order_confirmation"
    assert r["stage"] == "S3_confirm"
    assert len(r["candidates"]) >= 1
    assert r["offer"] is None


def test_s2_verification_failure():
    _, r = neg(customer_id="C-999", message="I want to return")
    assert r["status"] == "verification_failed"
    assert r["code"] == "customer_not_found"


def test_order_mismatch_rejected():
    """确认了别人的订单号 → 拒绝。"""
    _, first = neg(customer_id="C-001", message="I want to return")
    _, r = neg(session_id=first["session_id"], customer_id="C-001",
               message="yes", confirm_order_id="ORD-1006")
    assert r["status"] == "order_mismatch"


# ════════════════════════════════ 五个场景
def test_scenario_not_eligible_cites_rules():
    """场景①：不满足规则 → 附规则原文婉拒 + 给替代方案。"""
    _, r = full("C-003", "ORD-1003", "I want to return this jacket")
    assert r["scenario"] == "not_eligible"
    assert r["status"] == "declined"
    assert r["cited_rules"], "婉拒必须附规则原文"
    assert any(c["code"] == "R-WINDOW" for c in r["cited_rules"])
    assert r["offer"] is not None, "婉拒也要给台阶"


def test_scenario_usage_issue_sends_guide():
    """场景②：使用问题 → 手册/培训，不发钱。"""
    _, r = full("C-004", "ORD-1004", "I can't get it to pair, how to connect?")
    assert r["scenario"] == "usage_issue"
    assert r["action"] == "send_guide"
    assert r["offer"]["value"] == 0.0, "使用问题不该用钱解决"
    assert r["knowledge"]["manual_url"]
    assert len(r["knowledge"]["tips"]) >= 2


def test_scenario_value_gap_offers_compensation():
    """场景③：价值不符 → 补偿/优惠券，且不超 30% 上限。"""
    _, r = full("C-001", "ORD-1001", "It's too small, I want a return")
    assert r["scenario"] == "value_gap"
    assert r["action"] == "offer_compensation"
    assert r["offer"]["value"] <= 100.0 * C.MAX_DISCOUNT_PCT


def test_scenario_product_damage_offers_repair_or_exchange():
    """场景④：破损 → 修理/换货，且不受窗口限制。"""
    _, r = full("C-002", "ORD-1002", "The mug arrived cracked")
    assert r["scenario"] == "product_damage"
    assert r["action"] == "offer_repair_exchange"
    labels = [r["offer"]["label"]] + [a["label"] for a in r["alternatives"]]
    assert any("换新" in x or "维修" in x for x in labels)


def test_scenario_emotional_small_amount_instant_refund():
    """场景⑤a：情绪激烈 + 小额 → 立即退，且不再挽留。"""
    _, r = full("C-005", "ORD-1005", "This is ridiculous, just refund me NOW!")
    assert r["scenario"] == "emotional_insist"
    assert r["status"] == "instant_refund"
    assert r["refund"]["amount"] == 45.00
    assert r["offer"] is None, "情绪激烈时禁止再挽留"
    assert r["experience_hook"]["type"] == "comeback_credit"


def test_scenario_emotional_high_value_escalates_with_sla():
    """场景⑤b：情绪激烈 + 大额 → 人工介入，必须给死时效。"""
    _, r = full("C-006", "ORD-1006", "This is unacceptable, I want my money back NOW!")
    assert r["status"] == "escalated"
    assert r["ticket"]["sla_hours"] == C.MANUAL_REVIEW_SLA_HOURS
    assert r["ticket"]["priority"] == "P1"
    assert r["guarantee"]["return_window_frozen"] is True
    assert r["offer"] is None


def test_abuse_cooldown_skips_negotiation():
    """薅羊毛用户不再给券（final sale 先命中婉拒）。"""
    _, r = full("C-007", "ORD-1007", "I changed my mind, I want to return")
    assert r["scenario"] == "not_eligible"
    assert any(c["code"] == "R-FINAL" for c in r["cited_rules"])


# ════════════════════════════════ 四层护栏
def test_guardrail_l2_offer_not_in_allowlist():
    code, r = full("C-001", "ORD-1001", "too small, want a return", force="bad_offer")
    assert code == 422
    assert r["guardrail"]["layer"] == "L2"
    assert r["guardrail"]["code"] == "OFFER_NOT_IN_ALLOWLIST"


def test_guardrail_l3_forbidden_phrase():
    code, r = full("C-001", "ORD-1001", "too small, want a return", force="bad_text")
    assert code == 422
    assert r["guardrail"]["layer"] == "L3"
    assert r["guardrail"]["code"] == "FORBIDDEN_PHRASE"


def test_guardrail_l3_unapproved_amount():
    code, r = full("C-001", "ORD-1001", "too small, want a return", force="bad_amount")
    assert code == 422
    assert r["guardrail"]["layer"] == "L3"
    assert r["guardrail"]["code"] == "UNAPPROVED_AMOUNT"


def test_guardrail_l4_forged_token_rejected():
    import json, time
    payload = {"order_id": "ORD-1001", "offer_id": "X", "value": 999.0,
               "exp": int(time.time()) + 600, "jti": "x"}
    fake = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode().hex() + ".deadbeef" * 4
    r = client.post("/api/accept", json={"offer_token": fake, "idempotency_key": "k-evil"})
    assert r.status_code == 422
    assert r.json()["guardrail"]["code"] == "BAD_SIGNATURE"


def test_l4_execute_and_idempotent():
    _, r = full("C-001", "ORD-1001", "It's too small, I want a return")
    tok = r["offer_token"]
    a = client.post("/api/accept", json={"offer_token": tok, "idempotency_key": "k-0001"}).json()
    b = client.post("/api/accept", json={"offer_token": tok, "idempotency_key": "k-0001"}).json()
    assert a["status"] == "executed"
    assert b["status"] == "already_executed"
    assert a["executed_at"] == b["executed_at"], "重放不得二次发钱"


# ════════════════════════════════ 体验不变量（要求 4）
def test_every_reply_has_escape_hatch():
    for cid, oid, msg in [("C-001", "ORD-1001", "too small, return"),
                          ("C-004", "ORD-1004", "how to connect?"),
                          ("C-003", "ORD-1003", "I want to return")]:
        _, r = full(cid, oid, msg)
        assert r.get("escape_hatch"), f"{cid} 缺少退出出口"


def test_user_can_opt_out_anytime():
    _, first = neg(customer_id="C-001", message="I want to return")
    _, r = neg(session_id=first["session_id"], customer_id="C-001",
               message="just give me the refund", want_return_anyway=True)
    assert r["status"] == "released"
    assert r["release_reason"] == "user_opted_out"


def test_max_rounds_enforced():
    _, f = neg(customer_id="C-001", message="I want to return")
    sid = f["session_id"]
    for _ in range(C.MAX_NEGOTIATION_ROUNDS):
        neg(session_id=sid, customer_id="C-001", message="no", confirm_order_id="ORD-1001")
    _, r = neg(session_id=sid, customer_id="C-001", message="still no",
               confirm_order_id="ORD-1001")
    assert r["status"] == "released"
    assert r["release_reason"] == "max_rounds_reached"


def test_no_experience_violation_recorded():
    for cid, oid, msg in [("C-001", "ORD-1001", "too small, return"),
                          ("C-005", "ORD-1005", "This is ridiculous, refund NOW!"),
                          ("C-006", "ORD-1006", "Unacceptable! refund NOW!"),
                          ("C-003", "ORD-1003", "I want to return")]:
        full(cid, oid, msg)
    assert metrics.snapshot()["experience"]["experience_violations"] == 0


# ════════════════════════════════ 经济效率（要求 1）
def test_intent_never_uses_large_model():
    """意图识别只能走规则快路或小模型，绝不能用大模型。"""
    _, r = full("C-006", "ORD-1006", "Unacceptable! I want my money back NOW!")
    intent_call = r["model_calls"][0]
    assert intent_call["tier"] in ("none", "small")
    assert C.MODEL_LARGE not in intent_call["model"]


def test_rule_fastpath_is_free():
    _, r = full("C-001", "ORD-1001", "It's too small, I want a return")
    assert r["model_calls"][0]["model"] == "rule-fastpath"
    assert r["model_calls"][0]["cost_usd"] == 0.0


def test_routing_escalates_for_high_value_order():
    _, r = full("C-004", "ORD-1004", "I can't get it to pair, how to connect?")
    gen = r["model_calls"][-1]
    assert gen["tier"] == "large"
    assert gen["route_reason"] == "high_value_order_worth_the_spend"


def test_routing_uses_template_for_not_eligible():
    """婉拒走确定性模板，零 LLM 成本。"""
    _, r = full("C-003", "ORD-1003", "I want to return this jacket")
    gen = r["model_calls"][-1]
    assert gen["tier"] == "none"
    assert gen["cost_usd"] == 0.0


def test_avg_cost_well_under_case_budget():
    """case 给的预算是 $0.15/次对话，路由后应低一个数量级。"""
    for cid, oid, msg in [("C-001", "ORD-1001", "too small, return"),
                          ("C-002", "ORD-1002", "arrived cracked, return"),
                          ("C-003", "ORD-1003", "I want to return"),
                          ("C-004", "ORD-1004", "how to connect? want to return"),
                          ("C-005", "ORD-1005", "ridiculous, refund NOW!"),
                          ("C-006", "ORD-1006", "unacceptable, refund NOW!")]:
        full(cid, oid, msg)
    snap = metrics.snapshot()
    avg = snap["cost"]["avg_cost_per_conversation_usd"]
    assert avg < 0.015, f"均成本 {avg} 应低于预算的 1/10"


def test_confirmation_step_does_not_consume_negotiation_round():
    """确认订单不能占用谈判额度；让利阶梯必须到第二轮才加码。"""
    _, first = neg(customer_id="C-001", message="It's too small, I want a return")
    sid = first["session_id"]
    _, r1 = neg(session_id=sid, customer_id="C-001", message="too small",
                confirm_order_id="ORD-1001")
    _, r2 = neg(session_id=sid, customer_id="C-001", message="still want to return",
                confirm_order_id="ORD-1001")
    v1 = [o for o in [r1["offer"], *r1["alternatives"]] if "CREDIT" in o["offer_id"]]
    assert r1["model_calls"][-1]["route_reason"] != "second_round_stalemate", \
        "第一次给方案不该被算成第二轮"
    assert r2["status"] == "offer_made"
    assert any("25" in o["offer_id"] for o in
               [{"offer_id": r2["offer"]["offer_id"]}, *r2["alternatives"]]), \
        "第二轮应加码到 25%"


def test_metrics_reports_both_deflection_definitions():
    full("C-001", "ORD-1001", "It's too small, I want a return")
    ns = metrics.snapshot()["north_star"]
    assert "addressable_deflection_rate" in ns
    assert "overall_deflection_rate" in ns


# ════════════════════════════════ 回归：Code Review 修掉的 8 个问题
def test_ineligible_decline_never_routes_to_model():
    """情绪中档（0.45≤e<0.70）的不合规单曾绕过模板走大模型，
    产出一条没有规则依据的婉拒，而断言只认 status 所以静默放行。"""
    _, r = full("C-003", "ORD-1003", "I'm disappointed, I want to return this jacket")
    assert r["scenario"] == "not_eligible"
    assert r["status"] == "declined"
    assert r["cited_rules"], "婉拒必须附规则原文，不论情绪高低"
    assert r["model_calls"][-1]["tier"] == "none", "婉拒是确定性内容，不该花钱走模型"
    assert r.get("experience_warning") is None


def test_decline_invariant_keys_off_action_not_just_status():
    """哪怕以后又有新分支写出 status!='declined' 的婉拒，断言也要拦住。"""
    import guardrails as G
    with pytest.raises(G.GuardrailTripped) as e:
        G.assert_experience_invariants(
            {"escape_hatch": "x", "status": "offer_made",
             "action": "decline_with_rules"}, True)
    assert e.value.code == "DECLINE_WITHOUT_RULE"


def test_angry_user_with_ineligible_order_gets_no_retention():
    """情绪硬阈值必须压过合规性判定：曾因先判 eligible 而对发火用户继续推挽留。"""
    _, r = full("C-003", "ORD-1003", "This is ridiculous! I want my money back NOW! 投诉!")
    assert r["scenario"] == "emotional_insist"
    assert r["offer"] is None, "情绪超阈值禁止任何挽留 offer"
    # 不合规不能自动秒退，必须转人工，并把规则带给人工
    assert r["status"] == "escalated"
    assert "not_eligible" in r["ticket"]["anomalies"]
    assert any(c["code"] == "R-WINDOW" for c in r["ticket"]["cited_rules"])


def test_abuse_cooldown_releases_without_tripping_guardrail():
    """冷静期给的是空 offers，继续往下走会被 L2 白名单必然拦下，
    把一条正常业务路径变成 422 护栏拦截。"""
    code, r = full("C-007", "ORD-1008", "I changed my mind, I want to return")
    assert code == 200, "冷静期是业务决策，不是护栏故障"
    assert r["status"] == "released"
    assert r["release_reason"] == "abuse_cooldown"
    assert r["offer"] is None
    assert metrics.snapshot()["guardrail_trips"] == {}, "不该污染护栏指标"


def test_one_session_counts_as_one_conversation():
    """两轮挽留曾被记成两段会话，且会话累计成本被重复累加。"""
    _, first = neg(customer_id="C-001", message="It's too small, I want a return")
    sid = first["session_id"]
    last = None
    for msg in ("too small", "still want to return"):
        _, last = neg(session_id=sid, customer_id="C-001", message=msg,
                      confirm_order_id="ORD-1001")
    snap = metrics.snapshot()
    assert snap["conversations"] == 1
    assert sum(snap["actions"].values()) == 1
    assert snap["cost"]["llm_cost_usd_total"] == pytest.approx(last["cost_usd"]), \
        "看板成本必须等于会话实际累计成本"


def test_holdout_bucket_stable_across_processes():
    """曾用内建 hash()：字符串 hash 每进程带随机种子，同一会话换 worker 就换实验臂。"""
    import subprocess
    code = ("import sys; sys.path.insert(0,'.'); "
            "from app import _holdout_bucket; print(_holdout_bucket('S-abc123def456'))")
    root = str(Path(__file__).resolve().parents[1])
    outs = [subprocess.run([sys.executable, "-c", code], cwd=root, text=True,
                           capture_output=True).stdout.strip() for _ in range(3)]
    buckets = {int(o) for o in outs}      # 空输出（函数不存在）会直接 ValueError
    assert len(buckets) == 1, f"跨进程分桶不稳定：{outs}"
    assert 0 <= buckets.pop() < 100


def test_accept_stays_idempotent_after_restart(monkeypatch):
    """内存幂等表活不过重启，光靠它会对同一个 key 二次执行 = 二次发钱。"""
    import datetime as dt

    import db
    import store as S
    _, r = full("C-001", "ORD-1001", "It's too small, I want a return")
    tok = r["offer_token"]
    a = client.post("/api/accept",
                    json={"offer_token": tok, "idempotency_key": "k-boot"}).json()
    assert a["status"] == "executed"

    # 模拟重启：内存表清空，但库里那行还在
    row = {"order_id": a["order_id"], "offer_id": a["offer_id"], "value": a["value"],
           "executed_at": dt.datetime.fromtimestamp(a["executed_at"], dt.timezone.utc)}
    S.EXECUTED.clear()
    monkeypatch.setattr(db, "get_execution", lambda key: row if key == "k-boot" else None)

    b = client.post("/api/accept",
                    json={"offer_token": tok, "idempotency_key": "k-boot"}).json()
    assert b["status"] == "already_executed", "重启后重放不得二次发钱"
    assert b["executed_at"] == a["executed_at"]


def test_csat_rejects_out_of_range_scores():
    """无鉴权的写接口，放一个 99 进来就能把 avg_csat 带偏。"""
    assert client.post("/api/csat?score=5").status_code == 200
    for bad in (0, 6, 99, -3):
        assert client.post(f"/api/csat?score={bad}").status_code == 422, bad
    assert metrics.snapshot()["experience"]["avg_csat"] == 5.0


def test_used_item_blocks_only_no_reason_returns():
    """R-USED 原文是"不支持无理由退货"：使用类问题必须先用过才会发现，
    尺码试穿也不算明显使用，拿这条挡人会踩体验红线。"""
    import policy
    from models import ReturnReason as RR
    from store import ORDERS
    used = ORDERS["ORD-1004"]      # used=True，窗口内
    assert used["used"] is True
    assert policy.check_eligibility(used, RR.USAGE)["eligible"]
    assert policy.check_eligibility(used, RR.SIZE_FIT)["eligible"]
    assert policy.check_eligibility(used, RR.DAMAGED)["eligible"]
    blocked = policy.check_eligibility(used, RR.CHANGED_MIND)
    assert not blocked["eligible"]
    assert any(code == "R-USED" for code, _ in blocked["violated"])


def test_escalation_ticket_ids_are_unique():
    """ticket_id 曾只用秒级时间戳，同一秒内两次升级撞 id，
    入库是 ON CONFLICT DO NOTHING，第二张工单被静默丢掉。"""
    ids = [full("C-006", "ORD-1006", "This is unacceptable, refund NOW!")[1]["ticket"]["ticket_id"]
           for _ in range(3)]
    assert len(set(ids)) == 3, ids
