"""Return Saver — 退货挽留 Agent（MVP）

五阶段流程：S1 意图识别 → S2 用户/订单校验 → S3 确认订单 → S4 售后规则核验 → S5 场景执行
核心主张：LLM 有建议权，没有执行权；降退货率不得以损伤体验为代价。
"""
from __future__ import annotations

import hashlib
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

import config as C
import db
import guardrails as G
import llm
import metrics
import observability as obs
import policy
import store
from models import (Action, AcceptRequest, Intent, ModelTier, NegotiateRequest,
                    ReturnReason, Scenario, Stage)

@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    obs.flush()      # 短生命周期容器不 flush 会丢 trace
    db.close()


app = FastAPI(title="Return Saver", version="0.3.0", lifespan=lifespan)

HOLDOUT_PCT = C.HOLDOUT_PCT           # 生产置 10：对照组做增量归因
ESCAPE = "随时回「还是要退」，我立刻转标准退货，不会多问一句。"


# ════════════════════════════════════════════ 工具
def _ok(session: store.Session, payload: dict, allow_retention: bool = True) -> dict:
    """统一出口：补上体验不变量要求的字段，并在返回前自检。"""
    payload.setdefault("session_id", session.session_id)
    payload.setdefault("stage", session.stage.value)
    payload.setdefault("escape_hatch", ESCAPE)
    payload.setdefault("cost_usd", round(session.llm_cost_usd, 6))
    payload.setdefault("model_calls", session.model_calls)
    try:
        G.assert_experience_invariants(payload, allow_retention)
    except G.GuardrailTripped as e:
        metrics.record_guardrail(e.layer, e.code)
        session.guardrail_trips.append({"layer": e.layer, "code": e.code, "detail": e.detail})
        print(f"EXPERIENCE_VIOLATION code={e.code} session={session.session_id} detail={e.detail}")
        obs.score(session.session_id, "experience-violation", True,
                  data_type="BOOLEAN", comment=e.code)
        payload["experience_warning"] = {"code": e.code, "detail": e.detail}
    if payload.get("status") in ("offer_made", "declined", "instant_refund",
                                 "escalated", "released"):
        obs.score(session.session_id, "retention-outcome", payload["status"],
                  data_type="CATEGORICAL")
    store.persist(session)
    return payload


def _blocked(session: store.Session, trip: G.GuardrailTripped) -> JSONResponse:
    metrics.record_guardrail(trip.layer, trip.code)
    session.guardrail_trips.append({"layer": trip.layer, "code": trip.code, "detail": trip.detail})
    print(f"GUARDRAIL_TRIGGERED layer={trip.layer} code={trip.code} "
          f"session={session.session_id} detail={trip.detail}")
    obs.score(session.session_id, "guardrail-trip", True, data_type="BOOLEAN",
              comment=f"{trip.layer}:{trip.code}")
    store.persist(session)
    return JSONResponse(status_code=422, content={
        "session_id": session.session_id,
        "status": "guardrail_blocked",
        "guardrail": {"layer": trip.layer, "code": trip.code, "detail": trip.detail},
        "reply": "抱歉，这条我没法处理，已经为你转到标准退货流程，不会耽误你。",
        "offer": None,
        "next_action": "fallback_to_standard_return",
        "escape_hatch": ESCAPE,
    })


def _track(session: store.Session, meta: dict) -> None:
    session.llm_cost_usd += meta.get("cost_usd", 0.0)
    session.model_calls.append(meta)


def _holdout_bucket(session_id: str) -> int:
    """0-99 的稳定分桶。不能用内建 hash()——字符串 hash 每个进程带随机种子，
    同一个 session_id 换个 worker / 重启一次就会换实验臂，增量归因直接作废。"""
    return int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16) % 100


def _release(session: store.Session, reply: str, reason: str) -> dict:
    session.stage = Stage.CLOSED
    session.outcome = "released"
    metrics.record_conversation(session.session_id, session.scenario or "n/a",
                                Action.STANDARD_RETURN.value,
                                session.llm_cost_usd,
                                [m["tier"] for m in session.model_calls])
    return _ok(session, {"status": "released", "reply": reply, "offer": None,
                         "release_reason": reason, "next_action": "standard_return"})


# ════════════════════════════════════════════ 主入口
@app.post("/api/negotiate")
def negotiate(req: NegotiateRequest):
    """一次调用 = 一个 Langfuse span，session_id 把整段对话串起来。

    会话必须在打开 trace **之前**解析出来——否则首轮的 trace 会挂到 "new" 上，
    Langfuse 里就聚合不成一段完整对话。"""
    session = store.get_session(req.session_id) or store.new_session(req.customer_id)
    with obs.session_trace(session.session_id, req.customer_id, req.message) as span:
        out = _negotiate(req, session)
        if span is not None and not isinstance(out, JSONResponse):
            obs.update(span, output={"status": out.get("status"),
                                     "scenario": out.get("scenario"),
                                     "action": out.get("action")},
                       metadata={"cost_usd": out.get("cost_usd"),
                                 "stage": out.get("stage")})
        return out


def _negotiate(req: NegotiateRequest, session: store.Session):
    import deps as D
    deps = D.build_default()      # Task 6 会换成按租户构造

    if req.customer_id:
        session.customer_id = req.customer_id
    session.turns += 1

    # 体验出口最优先：任何时候用户说要退，立刻放行
    if req.want_return_anyway:
        return _release(session, "没问题，已经为你开启退货流程，物流单马上发到邮箱。",
                        "user_opted_out")
    # round 只统计"真正发出过的挽留轮次"——确认订单、澄清身份都不占额度
    if session.round >= C.MAX_NEGOTIATION_ROUNDS:
        return _release(session, "不耽误你了，退货流程已经开好。", "max_rounds_reached")
    if session.turns > C.MAX_SESSION_TURNS:
        return _release(session, "这单我直接给你走退货，不再占用你时间。", "max_turns_reached")

    # ── S1 意图识别（规则快路 → 小模型，永不用大模型）
    intent_res, meta = llm.classify_intent(req.message, deps)
    _track(session, meta)
    session.intent, session.reason = intent_res.intent.value, intent_res.reason.value
    session.emotion = max(session.emotion, intent_res.emotion)   # 情绪只升不降
    session.stage = Stage.VERIFY

    if intent_res.intent is Intent.OTHER:
        session.stage = Stage.CLOSED
        return _ok(session, {"status": "passthrough", "intent": session.intent,
                             "reply": "这个问题我转给人工客服同事，马上有人接。",
                             "offer": None, "next_action": "handoff_to_main_agent"})

    # ── S2 用户 / 订单校验
    customer = store.CUSTOMERS.get(session.customer_id or "")
    orders = store.orders_of(session.customer_id) if session.customer_id else []
    ok, why = policy.verify_customer_and_orders(customer, orders)
    if not ok:
        session.stage = Stage.CLOSED
        return _ok(session, {"status": "verification_failed", "code": why,
                             "reply": "我这边没查到对应的订单，方便提供一下订单号或下单邮箱吗？",
                             "offer": None, "next_action": "ask_for_identity"})

    # ── S3 和用户确认订单（必须显式确认，防止认错单动错钱）
    candidates = policy.pick_candidate_orders(orders)
    if not candidates:
        session.stage = Stage.CLOSED
        return _ok(session, {"status": "no_returnable_order",
                             "reply": "你名下近期没有可退的已签收订单，要我帮你查别的吗？",
                             "offer": None, "next_action": "handoff_to_main_agent"})

    order_id = req.confirm_order_id or session.order_id
    if not order_id:
        session.stage = Stage.CONFIRM
        session.candidate_orders = [o["order_id"] for o in candidates]
        return _ok(session, {
            "status": "awaiting_order_confirmation",
            "reply": "为了不弄错，先跟你确认一下是这单吗？",
            "candidates": [{"order_id": o["order_id"], "product": o["product"],
                            "total": o["total"],
                            "delivered_days_ago": o["days_since_delivery"]}
                           for o in candidates[:3]],
            "offer": None,
            "next_action": "reply_with_confirm_order_id",
        })

    order = store.ORDERS.get(order_id)
    if not order or order["customer_id"] != session.customer_id:
        return _ok(session, {"status": "order_mismatch",
                             "reply": "这个订单号和你的账号对不上，麻烦再核对一下。",
                             "offer": None, "next_action": "ask_for_identity"})
    session.order_id = order_id

    # 对照组：不做任何挽留，用于增量归因
    if HOLDOUT_PCT and _holdout_bucket(session.session_id) < HOLDOUT_PCT:
        session.scenario = "holdout"
        metrics.record_conversation(session.session_id, "holdout",
                                    Action.STANDARD_RETURN.value,
                                    session.llm_cost_usd,
                                    [m["tier"] for m in session.model_calls], holdout=True)
        session.stage = Stage.CLOSED
        return _ok(session, {"status": "holdout_control", "reply": "好的，已为你开启退货流程。",
                             "offer": None, "next_action": "standard_return"})

    # ── S4 售后规则核验
    session.stage = Stage.ELIGIBILITY
    reason = ReturnReason(session.reason)
    elig = policy.check_eligibility(order, reason, deps)

    # ── S5 场景分类 + 执行策略
    session.stage = Stage.EXECUTE
    scenario = policy.classify_scenario(order, reason, session.emotion, elig, deps)
    session.scenario = scenario.value

    # 情绪激烈场景：不生成任何挽留话术，直接分级执行（不消耗谈判额度）
    if scenario is Scenario.EMOTIONAL_INSIST:
        res = policy.build_resolution(order, customer, scenario, elig, session.round, deps)
        return _emotional(session, order, res["action"], res["payload"])

    # 先按"假如这是下一轮"算方案，确认真要挽留了才把额度记上去
    res = policy.build_resolution(order, customer, scenario, elig, session.round + 1, deps)
    action, offers, allow_retention = res["action"], res["offers"], res["allow_retention"]

    # 策略层判定不该再挽留（薅羊毛冷静期等）：直接放行标准退货。
    # 不能继续往下走——offers 为空会让 L2 白名单校验必然抛错，
    # 把一条正常业务路径变成 422 护栏拦截，还污染护栏指标。
    if action is Action.STANDARD_RETURN or not offers:
        return _release(session, "这单我直接给你走退货，不再给你推别的方案。",
                        res["payload"].get("reason", "no_offer_available"))

    session.round += 1     # 到这里才是真正的一轮挽留；让利阶梯按这个数加码

    # 生成侧动态路由
    tier, route_reason = llm.route_generation(
        scenario, session.emotion, order["total"], session.round,
        customer.get("tier", "normal"), session.llm_cost_usd, deps)

    ctx = {"customer_name": customer["name"], "product": order["product"],
           "scenario": scenario.value, "reason": session.reason,
           "days_since_delivery": order["days_since_delivery"],
           "user_message": req.message}
    raw, gmeta = llm.generate_copy(tier, ctx, offers, req.force)
    gmeta["route_reason"] = route_reason
    _track(session, gmeta)

    allowed_ids = {o["offer_id"] for o in offers}
    approved = {f"${o['value']:.2f}" for o in offers} | {"$0.00"}

    # 模板档：不过 LLM，直接渲染确定性文案
    if tier is ModelTier.NONE and not req.force:
        return _template_reply(session, order, scenario, action, offers,
                               res["payload"], allow_retention)

    # ── L2 / L3 护栏
    try:
        proposal = G.validate_proposal(raw, allowed_ids)
        offer = next(o for o in offers if o["offer_id"] == proposal.offer_id)
        G.validate_value_cap(offer, order)
        G.scan_output_text(proposal.message, approved)
    except G.GuardrailTripped as e:
        return _blocked(session, e)

    metrics.record_conversation(session.session_id, scenario.value, action.value,
                                session.llm_cost_usd,
                                [m["tier"] for m in session.model_calls])
    payload = {
        "status": "offer_made", "scenario": scenario.value, "action": action.value,
        "reply": proposal.message,
        "offer": {**offer},
        "offer_token": G.issue_offer_token(order_id, offer),
        "alternatives": [{"offer_id": o["offer_id"], "label": o["label"]}
                         for o in offers if o["offer_id"] != offer["offer_id"]],
        "next_action": "await_customer_decision",
    }
    if scenario is Scenario.USAGE_ISSUE and res["payload"].get("kb"):
        payload["knowledge"] = res["payload"]["kb"]
    return _ok(session, payload, allow_retention)


# ════════════════════════════════════════════ 确定性模板分支（零 LLM 成本）
def _template_reply(session: store.Session, order: dict, scenario: Scenario,
                    action: Action, offers: list[dict], pl: dict,
                    allow_retention: bool = True) -> dict:
    metrics.record_conversation(session.session_id, scenario.value, action.value,
                                session.llm_cost_usd,
                                [m["tier"] for m in session.model_calls])
    if scenario is Scenario.NOT_ELIGIBLE:
        cited = [{"code": c, "text": t} for c, t in pl["violated"]]
        alt = offers[0] if offers else None
        reply = ("我核对了一下，这单确实不符合退货条件，原因写在下面，你可以自己核对。"
                 "但不能就这么算了——下面这个方案你看行不行。")
        p = {"status": "declined", "scenario": scenario.value, "action": action.value,
             "reply": reply, "cited_rules": cited, "policy_note": pl["policy_note"],
             "offer": alt, "next_action": "await_customer_decision"}
        if alt:
            p["offer_token"] = G.issue_offer_token(order["order_id"], alt)
        return _ok(session, p, allow_retention)

    alt = offers[0] if offers else None
    p = {"status": "offer_made", "scenario": scenario.value, "action": action.value,
         "reply": "我先给你一个方案，你看合不合适。", "offer": alt,
         "next_action": "await_customer_decision"}
    if alt:
        p["offer_token"] = G.issue_offer_token(order["order_id"], alt)
    return _ok(session, p, allow_retention)


# ════════════════════════════════════════════ 情绪激烈：分级处理
def _emotional(session: store.Session, order: dict, action: Action, pl: dict) -> dict:
    session.stage = Stage.CLOSED
    metrics.record_conversation(session.session_id, Scenario.EMOTIONAL_INSIST.value,
                                action.value, session.llm_cost_usd,
                                [m["tier"] for m in session.model_calls])
    if action is Action.INSTANT_REFUND:
        session.outcome = "instant_refund"
        keep = pl.get("keep_item")
        reply = ("不跟你绕了，退款我已经直接发起，" + pl["eta"] + "。"
                 + ("商品你留着就行，不用寄回。" if keep else "退货单已发你邮箱，上门取件免费。")
                 + "这次是我们没做好，下次回来我给你留个补偿。")
        return _ok(session, {
            "status": "instant_refund", "scenario": Scenario.EMOTIONAL_INSIST.value,
            "action": action.value, "reply": reply,
            "refund": {"amount": pl["refund_amount"], "eta": pl["eta"],
                       "keep_item": keep},
            "experience_hook": {"type": "comeback_credit", "value": 10.0,
                                "note": "退款完成后自动发放，90 天有效——把差体验变成下次再来的理由"},
            "offer": None, "next_action": "refund_issued"}, allow_retention=False)

    # 大额 / 异常 → 人工介入，但必须给死时效
    session.outcome = "escalated"
    # ticket_id 必须带随机尾巴：只用秒级时间戳的话，同一秒内两次升级会撞 id，
    # 而入库是 ON CONFLICT (ticket_id) DO NOTHING —— 第二张工单会被静默丢掉
    ticket = {"ticket_id": f"T-{int(time.time())}-{uuid.uuid4().hex[:6]}",
              "order_id": order["order_id"],
              "priority": pl["priority"], "sla_hours": pl["sla_hours"],
              "anomalies": pl["anomalies"], "amount": order["total"],
              "created_at": int(time.time())}
    if pl.get("violated"):
        # 不合规却因情绪转人工：把违反的规则带上，人工才知道为什么不能直接退
        ticket["cited_rules"] = [{"code": c, "text": t} for c, t in pl["violated"]]
    store.MANUAL_QUEUE.append(ticket)
    db.enqueue_ticket(ticket, customer_id=session.customer_id,
                      session_id=session.session_id)
    reply = (f"这单金额比较大，我不想让机器替你做决定。已经转给专人，"
             f"{pl['sla_hours']} 小时内一定给你答复，工单号 {ticket['ticket_id']}。"
             f"在那之前退货权益不受影响，窗口我已经帮你冻结。")
    return _ok(session, {
        "status": "escalated", "scenario": Scenario.EMOTIONAL_INSIST.value,
        "action": action.value, "reply": reply, "ticket": ticket,
        "guarantee": {"return_window_frozen": True,
                      "sla_hours": pl["sla_hours"]},
        "offer": None, "next_action": "human_takeover"}, allow_retention=False)


# ════════════════════════════════════════════ L4 执行层
@app.post("/api/accept")
def accept(req: AcceptRequest):
    if req.idempotency_key in store.EXECUTED:
        return {"status": "already_executed", **store.EXECUTED[req.idempotency_key]}
    # 内存幂等表活不过重启，光靠它会在重启后对同一个 key 二次执行 = 二次发钱。
    # 落了库就必须回库里问一次，这是 L4 唯一真正防重放的地方。
    prior = db.get_execution(req.idempotency_key)
    if prior is not None:
        result = {"order_id": prior["order_id"], "offer_id": prior["offer_id"],
                  "value": float(prior["value"]),
                  "executed_at": int(prior["executed_at"].timestamp())}
        store.EXECUTED[req.idempotency_key] = result      # 回填，后续重放不再查库
        return {"status": "already_executed", **result}
    try:
        payload = G.verify_offer_token(req.offer_token)
    except G.GuardrailTripped as e:
        metrics.record_guardrail(e.layer, e.code)
        print(f"GUARDRAIL_TRIGGERED layer={e.layer} code={e.code} detail={e.detail}")
        return JSONResponse(status_code=422, content={
            "status": "guardrail_blocked",
            "guardrail": {"layer": e.layer, "code": e.code, "detail": e.detail},
            "reply": "这个方案已经失效了，我重新给你出一个。",
            "next_action": "reissue_offer"})
    result = {"order_id": payload["order_id"], "offer_id": payload["offer_id"],
              "value": payload["value"], "executed_at": int(time.time())}
    store.EXECUTED[req.idempotency_key] = result
    db.record_execution(req.idempotency_key, payload["order_id"],
                        payload["offer_id"], payload["value"])
    return {"status": "executed", **result}


# ════════════════════════════════════════════ 运维 / 看板
@app.get("/health")
def health():
    """报配置状态，绝不回显任何密钥本身。"""
    cfg = C.summary()
    cfg["ok"] = True
    cfg["llm_mode"] = "real" if C.USE_REAL_LLM else "mock"
    cfg["langfuse"]["client_ready"] = obs.enabled()
    cfg["store"] = db.ping()
    return cfg


@app.get("/api/metrics")
def get_metrics():
    return metrics.snapshot()


@app.get("/api/policy")
def get_policy():
    return store.MERCHANT_POLICY


@app.get("/api/manual-queue")
def manual_queue():
    if db.enabled():
        rows = db.open_tickets()
        return {"pending": len(rows), "source": "postgres", "tickets": rows}
    return {"pending": len(store.MANUAL_QUEUE), "source": "memory",
            "tickets": store.MANUAL_QUEUE}


@app.post("/api/csat")
def csat(score: int = Query(ge=1, le=5), session_id: str | None = None):
    """会话结束后的用户评分 1-5。同时进本地看板和 Langfuse scores。

    范围必须在接口层就框死：这是个无鉴权的写接口，放一个 99 进来
    就能把 avg_csat 这条对外指标彻底带偏。"""
    metrics.record_csat(score)
    if session_id:
        obs.score(session_id, "user-csat", float(score), data_type="NUMERIC")
    return {"ok": True, "recorded": score, "langfuse": obs.enabled()}
