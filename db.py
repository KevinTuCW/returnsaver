"""PostgreSQL 接入。

`RS_STORE_BACKEND=memory`（默认）时整个模块是 no-op，连接池都不会建——
MVP 与测试全程零依赖。切到 `postgres` 才真正连库。

只持久化丢了会出事的三张表：会话 / 执行幂等 / 人工工单。
"""
from __future__ import annotations

import json
from typing import Any

import config as C

_pool = None
_init_error: str | None = None


def _get_pool():
    global _pool, _init_error
    if not C.USE_POSTGRES:
        return None
    if _pool is not None:
        return _pool
    try:
        import logging

        from psycopg_pool import ConnectionPool
        # 池子连不上时会疯狂重试刷屏；降到 CRITICAL，失败由下面的 except 统一上报
        logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)
        _pool = ConnectionPool(
            C.DATABASE_URL, min_size=C.DB_POOL_MIN, max_size=C.DB_POOL_MAX,
            timeout=C.DB_CONNECT_TIMEOUT, open=True, kwargs={"autocommit": True},
            reconnect_timeout=C.DB_CONNECT_TIMEOUT)
        _pool.wait(timeout=C.DB_CONNECT_TIMEOUT)
    except Exception as e:
        _init_error = f"{type(e).__name__}: {e}"
        print(f"DB_INIT_FAILED {_init_error}")
        _pool = None
    return _pool


def enabled() -> bool:
    return C.USE_POSTGRES and _get_pool() is not None


def ping() -> dict:
    """/health 用。不抛异常，只报状态。"""
    if not C.USE_POSTGRES:
        return {"backend": "memory", "ok": True}
    pool = _get_pool()
    if pool is None:
        return {"backend": "postgres", "ok": False, "error": _init_error}
    try:
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"backend": "postgres", "ok": True}
    except Exception as e:
        return {"backend": "postgres", "ok": False, "error": f"{type(e).__name__}: {e}"}


def _exec(sql: str, params: tuple = ()) -> None:
    pool = _get_pool()
    if pool is None:
        return
    try:
        with pool.connection() as conn:
            conn.execute(sql, params)
    except Exception as e:
        print(f"DB_WRITE_FAILED {type(e).__name__}: {e}")


def _query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    pool = _get_pool()
    if pool is None:
        return []
    try:
        from psycopg.rows import dict_row
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                return cur.fetchall()
    except Exception as e:
        print(f"DB_READ_FAILED {type(e).__name__}: {e}")
        return []


# ──────────────────────────────────────────── 会话
UPSERT_SESSION = """
INSERT INTO sessions (session_id, customer_id, order_id, stage, round, turns,
                      intent, reason, emotion, scenario, llm_cost_usd,
                      model_calls, guardrail_trips, outcome,
                      tenant_id, config_version, updated_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s, now())
ON CONFLICT (session_id) DO UPDATE SET
    customer_id=EXCLUDED.customer_id, order_id=EXCLUDED.order_id,
    stage=EXCLUDED.stage, round=EXCLUDED.round, turns=EXCLUDED.turns,
    intent=EXCLUDED.intent, reason=EXCLUDED.reason, emotion=EXCLUDED.emotion,
    scenario=EXCLUDED.scenario, llm_cost_usd=EXCLUDED.llm_cost_usd,
    model_calls=EXCLUDED.model_calls, guardrail_trips=EXCLUDED.guardrail_trips,
    outcome=EXCLUDED.outcome, updated_at=now()
    -- tenant_id / config_version 刻意不在 DO UPDATE 里：钉住就是钉住。
    -- 让它们跟着 UPSERT 漂移就等于把快照语义悄悄取消掉。
"""


def save_session(s) -> None:
    if not enabled():
        return
    _exec(UPSERT_SESSION, (
        s.session_id, s.customer_id, s.order_id, s.stage.value, s.round, s.turns,
        s.intent, s.reason, s.emotion, s.scenario, s.llm_cost_usd,
        json.dumps(s.model_calls, ensure_ascii=False),
        json.dumps(s.guardrail_trips, ensure_ascii=False), s.outcome,
        s.tenant_id, s.config_version))


def load_session(session_id: str) -> dict | None:
    if not enabled():
        return None
    rows = _query("SELECT * FROM sessions WHERE session_id=%s", (session_id,))
    return rows[0] if rows else None


# ──────────────────────────────────────────── 执行幂等
def record_execution(key: str, order_id: str, offer_id: str, value: float,
                     session_id: str | None = None) -> None:
    if not enabled():
        return
    _exec("""INSERT INTO executions (idempotency_key, order_id, offer_id, value, session_id)
             VALUES (%s,%s,%s,%s,%s) ON CONFLICT (idempotency_key) DO NOTHING""",
          (key, order_id, offer_id, value, session_id))


def get_execution(key: str) -> dict | None:
    if not enabled():
        return None
    rows = _query("SELECT * FROM executions WHERE idempotency_key=%s", (key,))
    return rows[0] if rows else None


# ──────────────────────────────────────────── 人工工单
def enqueue_ticket(t: dict, customer_id: str | None = None,
                   session_id: str | None = None) -> None:
    if not enabled():
        return
    _exec("""INSERT INTO manual_tickets
             (ticket_id, session_id, order_id, customer_id, priority,
              sla_hours, amount, anomalies, due_at)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,
                     now() + make_interval(hours => %s))
             ON CONFLICT (ticket_id) DO NOTHING""",
          (t["ticket_id"], session_id, t["order_id"], customer_id, t["priority"],
           t["sla_hours"], t["amount"], json.dumps(t.get("anomalies", [])),
           t["sla_hours"]))


def open_tickets() -> list[dict]:
    if not enabled():
        return []
    return _query("""SELECT ticket_id, order_id, priority, sla_hours, amount,
                            status, created_at, due_at,
                            (due_at < now()) AS sla_breached
                     FROM manual_tickets WHERE status <> 'resolved'
                     ORDER BY due_at ASC""")


# ──────────────────────────────────────────── 商家配置
# 与 merchant.MerchantConfig 的字段一一对应。改这里必须同步改 merchant.py，
# 所以顺序也保持一致，方便对照。
CONFIG_COLS = ("return_window_days", "max_discount_pct", "instant_refund_cap_usd",
               "manual_sla_hours", "emotion_hard_stop", "emotion_large_model",
               "high_value_order_usd", "max_negotiation_rounds",
               "max_session_turns", "abuse_negotiation_limit", "exceptions_note")

# NUMERIC 从 psycopg 回来是 Decimal，下游一律按 float 用
_FLOAT_COLS = ("max_discount_pct", "instant_refund_cap_usd", "high_value_order_usd")

RULE_CODES = ("R-WINDOW", "R-FINAL", "R-HYGIENE", "R-USED", "R-DAMAGE")


def fetch_merchant_config(tenant_id: str, version: int | None) -> dict | None:
    """一行配置 + 它的 enabled 规则。version=None 取最新。

    只取 enabled 的规则：merchant.check_eligibility 用「code 在不在 rules 里」
    判断规则是否生效，停用的规则不该出现在这个 map 里。
    """
    if version is None:
        rows = _query("""SELECT * FROM rs_merchant_config
                         WHERE tenant_id=%s ORDER BY version DESC LIMIT 1""",
                      (tenant_id,))
    else:
        rows = _query("""SELECT * FROM rs_merchant_config
                         WHERE tenant_id=%s AND version=%s""",
                      (tenant_id, version))
    if not rows:
        return None
    row = dict(rows[0])
    rules = _query("""SELECT code, text FROM rs_merchant_rule
                      WHERE tenant_id=%s AND version=%s AND enabled=true""",
                   (tenant_id, row["version"]))
    out = {k: row[k] for k in CONFIG_COLS}
    for k in _FLOAT_COLS:
        out[k] = float(out[k])
    out["version"] = int(row["version"])
    out["rules"] = {r["code"]: r["text"] for r in rules}
    out["disabled_rules"] = [c for c in RULE_CODES if c not in out["rules"]]
    return out


def fetch_tenant(tenant_id: str) -> dict | None:
    rows = _query("SELECT * FROM rs_tenant WHERE tenant_id=%s", (tenant_id,))
    return dict(rows[0]) if rows else None


def insert_merchant_config(tenant_id: str, fields: dict, *, created_by: str,
                           note: str | None) -> int:
    """append-only 插入 version+1，规则一起写。返回新版本号。

    版本号在**同一个事务里**算：两个并发写各自先 SELECT MAX 再 INSERT 会拿到
    同一个 version 撞主键，其中一个直接失败。
    """
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("database unavailable")
    with pool.connection() as conn:
        with conn.transaction():
            cur = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM rs_merchant_config "
                "WHERE tenant_id=%s", (tenant_id,))
            version = int(cur.fetchone()[0])
            conn.execute(
                f"""INSERT INTO rs_merchant_config
                    (tenant_id, version, {', '.join(CONFIG_COLS)},
                     created_by, note)
                    VALUES (%s, %s, {', '.join(['%s'] * len(CONFIG_COLS))},
                            %s, %s)""",
                (tenant_id, version, *[fields[c] for c in CONFIG_COLS],
                 created_by, note))
            disabled = set(fields.get("disabled_rules") or [])
            for code, text in (fields.get("rules") or {}).items():
                conn.execute(
                    """INSERT INTO rs_merchant_rule
                       (tenant_id, version, code, text, enabled)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (tenant_id, version, code, text, code not in disabled))
    return version


# ──────────────────────────────────────────── 订单 / 客户属性
# 缺 attrs 行时的默认值。选取原则：**缺数据永不导致拒退**——少一个挽留选项
# 可以接受，凭空拒掉一个合规退货不行。所以会拦人的三个字段
# （final_sale / opened / used）一律默认 False。
ORDER_ATTR_DEFAULTS = {
    "sku": None, "product": None, "category": None, "gross_margin_pct": 0.5,
    "final_sale": False, "opened": False, "used": False,
    "sizes_in_stock": [], "repairable": False,
    "negotiations_last_90d": 0, "has_manual": False,
}

CUSTOMER_ATTR_DEFAULTS = {"tier": "normal", "lifetime_orders": 0,
                          "returns_last_90d": 0, "risk_flag": False}


def merge_order_attrs(order: dict, attrs: dict | None) -> dict:
    """把 helpmate 的 orders 行与 rs_order_attrs 合成策略引擎要的形状。

    days_since_delivery 由 delivered_at 现算：数据库里存「距今多少天」的整数
    第二天就是错的。mock 能蒙过去只因为它从不持久化。
    """
    import datetime as dt
    a = dict(ORDER_ATTR_DEFAULTS)
    if attrs:
        for k in ORDER_ATTR_DEFAULTS:
            if attrs.get(k) is not None:
                a[k] = attrs[k]
    delivered = (attrs or {}).get("delivered_at")
    if delivered is None:
        days = 0                      # 按"刚签收"算，不会因窗口被拒
    else:
        if delivered.tzinfo is None:
            delivered = delivered.replace(tzinfo=dt.timezone.utc)
        days = max(0, (dt.datetime.now(dt.timezone.utc) - delivered).days)
    return {**order, **a,
            "days_since_delivery": days,
            # 话术要有个能念出来的名字，退到 sku 再退到单号
            "product": a["product"] or a["sku"] or order["order_id"],
            "total": float(order["total"])}


def merge_customer_attrs(customer_id: str, attrs: dict | None) -> dict:
    """缺行时默认 normal / 非风险——不把陌生客户误判成薅羊毛。"""
    a = dict(CUSTOMER_ATTR_DEFAULTS)
    if attrs:
        for k in CUSTOMER_ATTR_DEFAULTS:
            if attrs.get(k) is not None:
                a[k] = attrs[k]
    return {"customer_id": customer_id,
            "name": (attrs or {}).get("name") or customer_id, **a}


_ORDER_SELECT = """SELECT o.order_id, o.customer_id, o.customer, o.status,
                          o.total, a.*
                   FROM orders o LEFT JOIN rs_order_attrs a USING (order_id)"""


def _order_row(r: dict) -> dict:
    return merge_order_attrs(
        {"order_id": r["order_id"], "customer_id": r["customer_id"],
         "status": r["status"], "total": r["total"]}, r)


def fetch_order(order_id: str, *, tenant_id: str,
                customer_id: str | None) -> dict | None:
    """带归属校验的订单读取。照 helpmate 的 get_order：报一个陌生单号只会
    得到「未找到」，而不是别人的订单。customer_id=None 表示无订单访问权。"""
    if customer_id is None:
        return None
    rows = _query(_ORDER_SELECT + """ WHERE o.order_id=%s AND o.tenant_id=%s
                                        AND o.customer_id=%s""",
                  (order_id, tenant_id, customer_id))
    return _order_row(dict(rows[0])) if rows else None


def fetch_orders_of(tenant_id: str, customer_id: str) -> list[dict]:
    rows = _query(_ORDER_SELECT + " WHERE o.tenant_id=%s AND o.customer_id=%s",
                  (tenant_id, customer_id))
    return [_order_row(dict(r)) for r in rows]


def fetch_customer(tenant_id: str, customer_id: str) -> dict | None:
    rows = _query("""SELECT * FROM rs_customer_attrs
                     WHERE tenant_id=%s AND customer_id=%s""",
                  (tenant_id, customer_id))
    return merge_customer_attrs(customer_id, dict(rows[0]) if rows else None)


def close() -> None:
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:
            pass
        _pool = None
