"""Return Saver — 退货挽留 Agent（MVP）

五阶段流程：S1 意图识别 → S2 用户/订单校验 → S3 确认订单 → S4 售后规则核验 → S5 场景执行
核心主张：LLM 有建议权，没有执行权；降退货率不得以损伤体验为代价。
"""
from __future__ import annotations

import hashlib
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from auth import Principal, require_admin_tenant, require_principal
import config as C
import db
import deps as D
import guardrails as G
import llm
import merchant_store as MS
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
WEB = Path(__file__).resolve().parent / "web"
app.mount("/admin/assets", StaticFiles(directory=WEB / "admin"), name="admin-assets")

HOLDOUT_PCT = C.HOLDOUT_PCT           # 生产置 10：对照组做增量归因
ESCAPE = "随时回「还是要退」，我立刻转标准退货，不会多问一句。"
ESCAPE_EN = 'Reply "I still want to return it" at any time and I will start the standard return immediately.'


def _is_english(text: str) -> bool:
    return bool(text and not any("\u4e00" <= char <= "\u9fff" for char in text)
                and any(char.isalpha() for char in text))


def _english_reply(payload: dict) -> str:
    status = payload.get("status")
    if status == "awaiting_order_confirmation":
        return "To make sure I have the right item, please confirm which order you want to return."
    if status == "verification_failed":
        return "I couldn't find a matching order. Could you share the order number or email used at checkout?"
    if status == "no_returnable_order":
        return "I couldn't find a recently delivered order that is eligible for return. Would you like help checking another order?"
    if status == "order_mismatch":
        return "That order number doesn't match your account. Please check it and try again."
    if status == "passthrough":
        return "I'll hand this over to a support specialist who can help you."
    if status in ("offer_made", "declined"):
        return "I'm sorry this didn't work out as expected. I can offer the option below. Would that work for you?"
    if status == "instant_refund":
        eta = (payload.get("refund") or {}).get("eta", "Your bank may take 1-3 business days to post it")
        return f"I've issued the refund immediately. {eta}."
    if status == "escalated":
        ticket = payload.get("ticket") or {}
        return (f"I don't want an automated decision on this order. A specialist will review it within "
                f"{ticket.get('sla_hours', 2)} hours. Your ticket is {ticket.get('ticket_id', 'open')}, "
                "and your return window is protected while you wait.")
    if status in ("released", "holdout_control"):
        return "I've started the standard return process for you. You won't need to answer any more questions."
    return payload.get("reply", "")


def _localize_offer(offer: dict | None) -> dict | None:
    if not offer:
        return offer
    labels = {
        "FREE_EXCHANGE": "Free size exchange with round-trip shipping included",
        "FREE_REPAIR": "Free repair with pickup and return shipping included",
        "SEND_GUIDE": "Setup guide and a 14-day return-window extension",
        "SEND_VIDEO_GUIDE": "90-second setup video and a 14-day return-window extension",
        "LIVE_ONBOARDING": "Free 10-minute one-to-one setup session",
        "GOODWILL_COUPON": "Goodwill coupon for a future purchase",
    }
    label = labels.get(offer.get("offer_id"))
    if not label and offer.get("type") == "store_credit":
        label = "Keep the item and receive store credit"
    if not label and offer.get("type") == "partial_refund":
        label = "Keep the item and receive a partial refund"
    return {**offer, "label": label or offer.get("label", "Available resolution")}


@app.get("/admin")
@app.get("/admin/")
def admin_shell():
    """Merchant app shell.

    Shopify is a delivery style here, not a platform dependency: no OAuth, no
    App Bridge token, just an embedded-app shaped shell that C1/C2/C3 can mount
    into later.
    """
    return FileResponse(WEB / "admin" / "index.html")


# ════════════════════════════════════════════ 工具
def _ok(session: store.Session, payload: dict, allow_retention: bool = True) -> dict:
    """统一出口：补上体验不变量要求的字段，并在返回前自检。"""
    payload.setdefault("session_id", session.session_id)
    payload.setdefault("stage", session.stage.value)
    payload.setdefault("escape_hatch", ESCAPE_EN if session.language == "en" else ESCAPE)
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
    if session.language == "en":
        if payload.get("reply") and not _is_english(payload["reply"]):
            payload["reply"] = _english_reply(payload)
        payload["offer"] = _localize_offer(payload.get("offer"))
        if payload.get("alternatives"):
            payload["alternatives"] = [_localize_offer(item) for item in payload["alternatives"]]
    reply = payload.get("reply")
    if reply:
        session.messages.append({"role": "agent", "text": reply, "at": int(time.time())})
        session.last_activity_at = time.time()
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


def _manual_review(session: store.Session, order: dict, *, sla_hours: int,
                   anomalies: list[str], priority: str = "P2",
                   cited_rules: list[tuple[str, str]] | None = None) -> dict:
    """Create at most one open review for a conversation and order.

    Conversation can continue after handoff; the business side effect cannot be
    duplicated by retries or by a customer returning later.
    """
    existing = next((t for t in store.MANUAL_QUEUE
                     if t.get("tenant_id") == session.tenant_id
                     and t.get("session_id") == session.session_id
                     and t.get("order_id") == order["order_id"]
                     and t.get("status", "open") != "resolved"), None)
    if existing is None:
        existing = db.get_open_ticket(session.tenant_id, session.session_id,
                                      order["order_id"])
    if existing is None:
        existing = {
            "ticket_id": f"T-{int(time.time())}-{uuid.uuid4().hex[:6]}",
            "tenant_id": session.tenant_id,
            "session_id": session.session_id,
            "order_id": order["order_id"],
            "priority": priority,
            "sla_hours": sla_hours,
            "anomalies": anomalies,
            "amount": order["total"],
            "created_at": int(time.time()),
            "status": "open",
        }
        if cited_rules:
            existing["cited_rules"] = [
                {"code": code, "text": text} for code, text in cited_rules]
        try:
            existing = db.enqueue_ticket(
                existing, tenant_id=session.tenant_id,
                customer_id=session.customer_id, session_id=session.session_id)
        except Exception as e:
            print(f"TICKET_TRANSACTION_FAILED {type(e).__name__}: {e}")
            raise HTTPException(
                status_code=503,
                detail="manual review could not be committed; retry safely")
        if not any(t.get("ticket_id") == existing["ticket_id"]
                   for t in store.MANUAL_QUEUE):
            store.MANUAL_QUEUE.append(existing)

    session.stage = Stage.EXECUTE
    session.outcome = "escalated"
    reply = (f"I have sent this request to a specialist for review. You will "
             f"receive an update within {sla_hours} hours. Your case number is "
             f"{existing['ticket_id']}, and your return window is paused while "
             "we review it." if session.language == "en" else
             f"这单已经转给售后专员复核，{sla_hours} 小时内会给你明确答复。"
             f"工单号 {existing['ticket_id']}，复核期间退货窗口已暂停计算。")
    return _ok(session, {
        "status": "escalated", "scenario": session.scenario,
        "action": Action.ESCALATE_HUMAN.value, "reply": reply,
        "ticket": existing,
        "cited_rules": existing.get("cited_rules", []),
        "guarantee": {"return_window_frozen": True, "sla_hours": sla_hours},
        "offer": None, "next_action": "human_review",
    }, allow_retention=False)


# ════════════════════════════════════════════ 主入口
@app.post("/api/negotiate")
def negotiate(req: NegotiateRequest,
              principal: Principal = Depends(require_principal)):
    """一次调用 = 一个 Langfuse span，session_id 把整段对话串起来。

    会话必须在打开 trace **之前**解析出来——否则首轮的 trace 会挂到 "new" 上，
    Langfuse 里就聚合不成一段完整对话。"""
    session = store.get_session(req.session_id)
    if session is None:
        # 新会话：钉住该租户当前版本。这是快照语义的锚点。
        session = store.new_session(
            req.customer_id, tenant_id=principal.tenant_id,
            config_version=MS.current_version(principal.tenant_id))
    elif session.tenant_id != principal.tenant_id:
        # 会话是租户级资源。拿 B 租户的 key 续 A 租户的会话就是横向越权，
        # 与 helpmate 在 002_order_ownership 里学到的是同一课，换个对象重演。
        raise HTTPException(status_code=403,
                            detail="session belongs to another tenant")
    # key 若绑定到具体客户（"tenant:customer"），它只能代表那个客户说话。
    # 租户级 key（服务间调用）不带客户身份，客户主体由上游鉴权后透传。
    if principal.customer_id and req.customer_id \
            and principal.customer_id != req.customer_id:
        raise HTTPException(status_code=403,
                            detail="api key is bound to another customer")
    deps = D.build(principal, session)
    session.messages.append({"role": "customer", "text": req.message,
                             "at": int(time.time())})
    session.last_activity_at = time.time()
    with obs.session_trace(session.session_id, req.customer_id, req.message) as span:
        out = _negotiate(req, session, deps)
        if span is not None and not isinstance(out, JSONResponse):
            obs.update(span, output={"status": out.get("status"),
                                     "scenario": out.get("scenario"),
                                     "action": out.get("action")},
                       metadata={"cost_usd": out.get("cost_usd"),
                                 "stage": out.get("stage")})
        return out


def _negotiate(req: NegotiateRequest, session: store.Session,
               deps: D.RetentionDeps):
    if session.turns == 0:
        session.language = "en" if _is_english(req.message) else "zh"
    if req.customer_id:
        session.customer_id = req.customer_id
    session.turns += 1

    # 体验出口最优先：任何时候用户说要退，立刻放行
    if req.want_return_anyway:
        return _release(session, "没问题，已经为你开启退货流程，物流单马上发到邮箱。",
                        "user_opted_out")
    # round 只统计"真正发出过的挽留轮次"——确认订单、澄清身份都不占额度
    if session.round >= deps.config.max_negotiation_rounds:
        return _release(session, "不耽误你了，退货流程已经开好。", "max_rounds_reached")
    if session.turns > deps.config.max_session_turns:
        return _release(session, "这单我直接给你走退货，不再占用你时间。", "max_turns_reached")

    # ── S1 意图识别（规则快路 → 小模型，永不用大模型）
    # 订单确认是上一轮退货意图的延续。只说 "Yes, ORD-1001" 本身不像退货请求，
    # 若重新分类会被判成 OTHER，插件随后 passthrough 回 RAG，确认流程永远走不完。
    if not (req.confirm_order_id and session.intent and session.reason):
        intent_res, meta = llm.classify_intent(req.message, deps)
        _track(session, meta)
        session.intent, session.reason = intent_res.intent.value, intent_res.reason.value
        session.emotion = max(session.emotion, intent_res.emotion)   # 情绪只升不降
        if intent_res.intent is Intent.OTHER:
            session.stage = Stage.CLOSED
            return _ok(session, {"status": "passthrough", "intent": session.intent,
                                 "reply": "这个问题我转给人工客服同事，马上有人接。",
                                 "offer": None, "next_action": "handoff_to_main_agent"})
    session.stage = Stage.VERIFY

    # ── S2 用户 / 订单校验
    customer = deps.get_customer(session.customer_id or "")
    orders = deps.get_orders_of(session.customer_id) if session.customer_id else []
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

    order = deps.get_order(order_id)
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

    # An ineligible return is a policy decision, not an automatic approval.
    # Escalate once so a specialist can consider evidence and exceptions while
    # the customer remains free to continue the conversation.
    if scenario is Scenario.NOT_ELIGIBLE:
        return _manual_review(
            session, order, sla_hours=deps.config.manual_sla_hours,
            anomalies=["not_eligible"], cited_rules=elig["violated"])

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
           "language": "English" if session.language == "en" else "Chinese",
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
                               res["payload"], deps, allow_retention)

    # ── L2 / L3 护栏
    try:
        proposal = G.validate_proposal(raw, allowed_ids)
        offer = next(o for o in offers if o["offer_id"] == proposal.offer_id)
        G.validate_value_cap(offer, order, deps)
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
        "offer_token": G.issue_offer_token(order_id, offer, deps,
                                           session_id=session.session_id),
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
                    deps: D.RetentionDeps, allow_retention: bool = True) -> dict:
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
            p["offer_token"] = G.issue_offer_token(
                order["order_id"], alt, deps, session_id=session.session_id)
        return _ok(session, p, allow_retention)

    alt = offers[0] if offers else None
    p = {"status": "offer_made", "scenario": scenario.value, "action": action.value,
         "reply": "我先给你一个方案，你看合不合适。", "offer": alt,
         "next_action": "await_customer_decision"}
    if alt:
        p["offer_token"] = G.issue_offer_token(
            order["order_id"], alt, deps, session_id=session.session_id)
    return _ok(session, p, allow_retention)


# ════════════════════════════════════════════ 情绪激烈：分级处理
def _emotional(session: store.Session, order: dict, action: Action, pl: dict) -> dict:
    session.stage = Stage.EXECUTE
    metrics.record_conversation(session.session_id, Scenario.EMOTIONAL_INSIST.value,
                                action.value, session.llm_cost_usd,
                                [m["tier"] for m in session.model_calls])
    if action is Action.INSTANT_REFUND:
        execution_key = f"instant-refund:{session.session_id}:{order['order_id']}"
        memory_key = (session.tenant_id, execution_key)
        if memory_key not in store.EXECUTED:
            executed_at = int(time.time())
            result = {"order_id": order["order_id"], "offer_id": "INSTANT_REFUND",
                      "value": pl["refund_amount"],
                      "session_id": session.session_id,
                      "executed_at": executed_at}
            try:
                if C.USE_POSTGRES:
                    _, row = db.execute_once(
                        tenant_id=session.tenant_id, key=execution_key,
                        token_jti=execution_key, order_id=order["order_id"],
                        offer_id="INSTANT_REFUND", value=pl["refund_amount"],
                        session_id=session.session_id, outcome="instant_refund")
                    result["executed_at"] = int(row["executed_at"].timestamp())
            except Exception as e:
                print(f"REFUND_TRANSACTION_FAILED {type(e).__name__}: {e}")
                raise HTTPException(
                    status_code=503,
                    detail="refund could not be committed; retry safely")
            store.EXECUTED[memory_key] = result
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

    return _manual_review(
        session, order, sla_hours=pl["sla_hours"],
        anomalies=pl["anomalies"], priority=pl["priority"],
        cited_rules=pl.get("violated"))


# ════════════════════════════════════════════ L4 执行层
def _accept_get_order(principal: Principal):
    """accept 没有会话上下文，只按租户取单。

    不做客户归属校验是有意的：调用方持有的 HMAC offer_token 是在一个已通过
    归属校验的会话里签发的，token 本身就是凭据。这里查订单是为了让 L4 重算
    金额上限，不是为了鉴权。租户边界仍然守着。
    """
    if db.enabled():
        return lambda oid: db.fetch_order_for_tenant(oid, principal.tenant_id)
    return lambda oid: store.ORDERS.get(oid)


@app.post("/api/accept")
def accept(req: AcceptRequest,
           principal: Principal = Depends(require_principal)):
    memory_key = (principal.tenant_id, req.idempotency_key)
    if memory_key in store.EXECUTED:
        return {"status": "already_executed", **store.EXECUTED[memory_key]}
    prior = db.get_execution(principal.tenant_id, req.idempotency_key)
    if prior is not None:
        result = {"order_id": prior["order_id"], "offer_id": prior["offer_id"],
                  "value": float(prior["value"]),
                  "executed_at": int(prior["executed_at"].timestamp())}
        store.EXECUTED[memory_key] = result
        return {"status": "already_executed", **result}
    try:
        payload = G.verify_offer_token(
            req.offer_token,
            # accept 没有会话上下文，所以自己按 principal 取单（仍带归属校验）
            get_order=_accept_get_order(principal),
            # 按 token 里的 cfg_v 回查那一版——快照语义在这里闭合。
            # 商家在 TTL 内调低上限，不会否掉系统已经承诺给客户的方案；
            # 但也不是免检：超过签发那一版的上限照样拦。
            load_config=lambda v: MS.load(principal.tenant_id, v)[0])
    except G.GuardrailTripped as e:
        metrics.record_guardrail(e.layer, e.code)
        print(f"GUARDRAIL_TRIGGERED layer={e.layer} code={e.code} detail={e.detail}")
        return JSONResponse(status_code=422, content={
            "status": "guardrail_blocked",
            "guardrail": {"layer": e.layer, "code": e.code, "detail": e.detail},
            "reply": "这个方案已经失效了，我重新给你出一个。",
            "next_action": "reissue_offer"})
    if payload.get("tenant_id") != principal.tenant_id:
        raise HTTPException(status_code=403, detail="offer belongs to another tenant")
    session_id = payload.get("session_id")
    session = store.get_session(session_id)
    if not session or session.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=409, detail="offer session is unavailable")

    result = {"order_id": payload["order_id"], "offer_id": payload["offer_id"],
              "value": payload["value"], "session_id": session_id,
              "executed_at": int(time.time())}
    jti_key = (principal.tenant_id, f"jti:{payload['jti']}")
    prior_jti = store.EXECUTED.get(jti_key)
    if prior_jti:
        store.EXECUTED[memory_key] = prior_jti
        return {"status": "already_executed", **prior_jti}

    try:
        if C.USE_POSTGRES:
            created, row = db.execute_once(
                tenant_id=principal.tenant_id, key=req.idempotency_key,
                token_jti=payload["jti"], order_id=payload["order_id"],
                offer_id=payload["offer_id"], value=payload["value"],
                session_id=session_id, outcome="retained")
            result = {
                "order_id": row["order_id"], "offer_id": row["offer_id"],
                "value": float(row["value"]), "session_id": row.get("session_id"),
                "executed_at": int(row["executed_at"].timestamp()),
            }
        else:
            created = True
    except Exception as e:
        print(f"EXECUTION_TRANSACTION_FAILED {type(e).__name__}: {e}")
        raise HTTPException(status_code=503,
                            detail="execution could not be committed; retry safely")

    store.EXECUTED[memory_key] = result
    store.EXECUTED[jti_key] = result
    if created:
        session.stage = Stage.EXECUTE
        session.outcome = "retained"
        session.messages.append({
            "role": "system", "text": f"Offer {payload['offer_id']} accepted and executed.",
            "at": int(time.time()), "event": "offer_executed"})
        session.last_activity_at = time.time()
        store.persist(session)
    return {"status": "executed" if created else "already_executed", **result}


# ════════════════════════════════════════════ 运维 / 看板
@app.get("/health")
def health():
    """报配置状态，绝不回显任何密钥本身。"""
    cfg = C.summary()
    cfg["ok"] = True
    cfg["llm_mode"] = "real" if C.USE_REAL_LLM else "mock"
    cfg["langfuse"]["client_ready"] = obs.enabled()
    cfg["store"] = db.ping()

    # 配置来源必须可见：跑「库里的配置」和跑「内置默认」行为不同，
    # 静默用默认是最难查的一类故障——看一眼就知道比翻日志强。
    tenant = C.DEFAULT_TENANT
    snapshot, degraded = MS.load(tenant, None)
    cfg["config"] = {
        "source": "database" if (db.enabled() and snapshot.version) else "builtin",
        "degraded": degraded,
        "tenant": tenant,
        "version": snapshot.version,
    }
    if degraded:
        cfg["ok"] = False
    return cfg


@app.get("/api/metrics")
def get_metrics(_: Principal = Depends(require_admin_tenant)):
    return metrics.snapshot()


@app.get("/api/policy")
def get_policy(principal: Principal = Depends(require_admin_tenant)):
    cfg, degraded = MS.load(principal.tenant_id, None)
    if cfg.version == 0:
        rules = dict(store.MERCHANT_POLICY["rules"])
        note = store.MERCHANT_POLICY["exceptions_note"]
    else:
        rules = dict(cfg.rules)
        note = cfg.exceptions_note
    extras = (db.fetch_policy_extras(principal.tenant_id, cfg.version)
              if db.enabled() and cfg.version else
              store.POLICY_EXTRAS.get(principal.tenant_id, {}))
    return {
        "tenant_id": principal.tenant_id, "version": cfg.version,
        "degraded": degraded, "return_window_days": cfg.return_window_days,
        "rules": rules, "exceptions_note": note,
        "special_rules": extras.get("special_rules", []),
        "auto_refund": extras.get("auto_refund", {
            **store.MERCHANT_POLICY["auto_refund"],
            "max_order_amount_usd": cfg.instant_refund_cap_usd,
        }),
    }


@app.put("/api/policy")
def update_policy(payload: dict = Body(...),
                  principal: Principal = Depends(require_admin_tenant)):
    """Validate and publish the tenant's actual versioned decision policy."""
    window = payload.get("return_window_days")
    if not isinstance(window, int) or not 1 <= window <= 365:
        raise HTTPException(status_code=422, detail="return_window_days must be 1-365")
    rules = payload.get("rules")
    if not isinstance(rules, dict) or not rules or any(
            not isinstance(k, str) or not isinstance(v, str) or not v.strip()
            for k, v in rules.items()):
        raise HTTPException(status_code=422, detail="rules must be a non-empty text map")
    auto = payload.get("auto_refund", {})
    if not isinstance(auto, dict):
        raise HTTPException(status_code=422, detail="auto_refund must be an object")
    try:
        minimum = float(auto.get("min_order_amount_usd", 0))
        maximum = float(auto.get("max_order_amount_usd", 50))
        attempts = int(auto.get("minimum_prior_attempts", 1))
        keep_item = float(auto.get("keep_item_below_usd", 20))
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(status_code=422, detail="automatic refund values must be numeric")
    if minimum < 0 or maximum < minimum or maximum > 500:
        raise HTTPException(status_code=422, detail="automatic refund amount range must be between $0 and $500")
    if not 0 <= attempts <= 3:
        raise HTTPException(status_code=422, detail="minimum prior attempts must be between 0 and 3")
    if keep_item < 0 or keep_item > maximum:
        raise HTTPException(status_code=422, detail="keep-item threshold must be within the refund range")
    special_rules = payload.get("special_rules", [])
    if not isinstance(special_rules, list) or any(
            not isinstance(item, dict) or not str(item.get("scope", "")).strip()
            or not str(item.get("match", "")).strip()
            or not str(item.get("rule", "")).strip()
            for item in special_rules):
        raise HTTPException(status_code=422, detail="special_rules must be complete objects")

    current, _ = MS.load(principal.tenant_id, None)
    fields = current.as_fields()
    fields.update({
        "return_window_days": window,
        "instant_refund_cap_usd": maximum,
        "rules": {**dict(current.rules), **{k: v.strip() for k, v in rules.items()}},
        "exceptions_note": str(payload.get("exceptions_note", "")).strip(),
    })
    fields["disabled_rules"] = [
        code for code in fields["disabled_rules"] if code not in fields["rules"]]
    auto_policy = {
        "enabled": bool(auto.get("enabled", False)),
        "min_order_amount_usd": minimum,
        "max_order_amount_usd": maximum,
        "minimum_prior_attempts": attempts,
        "keep_item_below_usd": keep_item,
        "execution_mode": "automatic_when_qualified",
    }
    fields["special_rules"] = special_rules
    fields["auto_refund"] = auto_policy
    try:
        version = MS.save(principal.tenant_id, fields,
                          created_by="admin-url", note="Published from Admin Policy")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    store.POLICY_EXTRAS[principal.tenant_id] = {
        "special_rules": special_rules,
        "auto_refund": auto_policy,
    }
    return {"ok": True, "version": version,
            "policy": get_policy(principal)}


@app.get("/api/manual-queue")
def manual_queue(principal: Principal = Depends(require_admin_tenant)):
    if db.enabled():
        rows = db.open_tickets(principal.tenant_id)
        return {"pending": len(rows), "source": "postgres", "tickets": rows}
    rows = [t for t in store.MANUAL_QUEUE
            if t.get("tenant_id") == principal.tenant_id]
    return {"pending": len(rows), "source": "memory", "tickets": rows}


def _active_handle_seconds(session: store.Session) -> float:
    """Conversation work time excluding customer-away gaps over five minutes."""
    timestamps = sorted(
        float(message["at"]) for message in session.messages
        if isinstance(message.get("at"), (int, float)))
    return sum(min(max(current - previous, 0), 300)
               for previous, current in zip(timestamps, timestamps[1:]))


@app.get("/admin/api/dashboard")
def admin_dashboard(range: str = Query("today", pattern="^(today|week|month)$"),
                    principal: Principal = Depends(require_admin_tenant)):
    now = time.time()
    span = {"today": 86400, "week": 7 * 86400, "month": 30 * 86400}[range]
    rows = [s for s in store.all_sessions(principal.tenant_id)
            if s.created_at >= now - span]
    orders = [store.ORDERS[s.order_id] for s in rows if s.order_id in store.ORDERS]
    escalated = [s for s in rows if s.outcome == "escalated"]
    retained = [s for s in rows if s.order_id in store.ORDERS
                and s.outcome == "retained"]
    bucket_count = 12 if range == "today" else (7 if range == "week" else 30)
    bucket_span = span / bucket_count
    series = []
    for index in range_builtin(bucket_count):
        start = now - span + index * bucket_span
        end = start + bucket_span
        bucket = [s for s in rows if start <= s.created_at < end]
        if range == "today":
            label = time.strftime("%H:%M", time.localtime(start))
        else:
            label = time.strftime("%b %d", time.localtime(start))
        series.append({"label": label, "handled": len(bucket),
                       "retained": len([s for s in bucket if s in retained]),
                       "escalated": len([s for s in bucket if s in escalated])})
    durations = [_active_handle_seconds(s) for s in rows]
    costs = [s.llm_cost_usd for s in rows]
    if db.enabled():
        executions = db.list_executions(principal.tenant_id, now - span)
    else:
        executions = list({
            (value.get("order_id"), value.get("offer_id"), value.get("executed_at")): value
            for value in store.EXECUTED.values()
            if value.get("executed_at", 0) >= now - span
        }.values())
    retained_order_ids = {
        execution.get("order_id") for execution in executions
        if execution.get("offer_id") != "INSTANT_REFUND"
    }
    # This KPI is deliberately an estimate: include executed retention actions
    # plus unresolved, addressable offers, but count each order only once. Older
    # dashboards summed sessions and could count the same order several times.
    retained_order_ids.update(
        s.order_id for s in rows
        if s.order_id and s.outcome is None
        and s.scenario in (Scenario.VALUE_GAP.value, Scenario.USAGE_ISSUE.value))
    estimated_saved = sum(
        store.ORDERS[order_id]["total"] for order_id in retained_order_ids
        if order_id in store.ORDERS)
    scenarios = {}
    outcomes = {}
    for s in rows:
        scenarios[s.scenario or "unclassified"] = scenarios.get(s.scenario or "unclassified", 0) + 1
        outcome = s.outcome or ("retained" if s in retained else "in_progress")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return {
        "range": range,
        "kpis": {"agent_takeovers": len(rows), "orders_processed": len({o["order_id"] for o in orders}),
                 "estimated_loss_saved_usd": round(estimated_saved, 2),
                 "human_escalations": len(escalated),
                 "avg_handle_seconds": round(sum(durations) / max(len(durations), 1)),
                 "avg_session_cost_usd": round(sum(costs) / max(len(costs), 1), 6)},
        "series": series, "scenarios": scenarios, "outcomes": outcomes,
    }


range_builtin = range


@app.get("/admin/api/chats")
def admin_chats(q: str = "", customer: str = "", product: str = "",
                order_id: str = "", from_ts: int | None = None,
                to_ts: int | None = None,
                principal: Principal = Depends(require_admin_tenant)):
    """Merchant-facing conversation index for the admin shell.

    This intentionally stays summary-shaped: enough for list/detail management
    UI, without exposing raw prompts or offer tokens. C3 can extend it with
    persisted turns once the history store lands.
    """
    rows = [session for session in store.all_sessions(principal.tenant_id)
            if session.messages]
    sessions = []
    for s in rows:
        order = store.ORDERS.get(s.order_id or "")
        customer_record = store.CUSTOMERS.get(s.customer_id or "")
        item = {
            "session_id": s.session_id,
            "customer_id": s.customer_id,
            "customer_name": customer_record.get("name") if customer_record else None,
            "tenant_id": s.tenant_id,
            "order_id": s.order_id,
            "product": order.get("product") if order else None,
            "stage": s.stage.value,
            "scenario": s.scenario,
            "outcome": s.outcome,
            "round": s.round,
            "turns": s.turns,
            "emotion": round(s.emotion, 2),
            "cost_usd": round(s.llm_cost_usd, 6),
            "model_calls": len(s.model_calls),
            "guardrail_trips": len(s.guardrail_trips),
            "created_at": int(s.created_at),
            "last_activity_at": int(s.last_activity_at),
            "messages": s.messages,
        }
        haystack = " ".join([str(item.get(k) or "") for k in
                            ("session_id", "customer_id", "customer_name", "order_id", "product")] +
                           [m.get("text", "") for m in s.messages]).lower()
        if q and q.lower() not in haystack:
            continue
        if customer and customer.lower() not in f"{item['customer_id']} {item['customer_name']}".lower():
            continue
        if product and product.lower() not in str(item["product"] or "").lower():
            continue
        if order_id and order_id.lower() not in str(item["order_id"] or "").lower():
            continue
        if from_ts and item["created_at"] < from_ts:
            continue
        if to_ts and item["created_at"] > to_ts:
            continue
        sessions.append(item)
    return {
        "source": "memory",
        "total": len(sessions),
        "sessions": sessions[:50],
        "note": "Conversation history from the active session store.",
    }


@app.post("/api/csat")
def csat(score: int = Query(ge=1, le=5), session_id: str | None = None):
    """会话结束后的用户评分 1-5。同时进本地看板和 Langfuse scores。

    范围必须在接口层就框死：这是个无鉴权的写接口，放一个 99 进来
    就能把 avg_csat 这条对外指标彻底带偏。"""
    metrics.record_csat(score)
    if session_id:
        obs.score(session_id, "user-csat", float(score), data_type="NUMERIC")
    return {"ok": True, "recorded": score, "langfuse": obs.enabled()}
