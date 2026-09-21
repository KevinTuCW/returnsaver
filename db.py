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
                      model_calls, guardrail_trips, outcome, updated_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s, now())
ON CONFLICT (session_id) DO UPDATE SET
    customer_id=EXCLUDED.customer_id, order_id=EXCLUDED.order_id,
    stage=EXCLUDED.stage, round=EXCLUDED.round, turns=EXCLUDED.turns,
    intent=EXCLUDED.intent, reason=EXCLUDED.reason, emotion=EXCLUDED.emotion,
    scenario=EXCLUDED.scenario, llm_cost_usd=EXCLUDED.llm_cost_usd,
    model_calls=EXCLUDED.model_calls, guardrail_trips=EXCLUDED.guardrail_trips,
    outcome=EXCLUDED.outcome, updated_at=now()
"""


def save_session(s) -> None:
    if not enabled():
        return
    _exec(UPSERT_SESSION, (
        s.session_id, s.customer_id, s.order_id, s.stage.value, s.round, s.turns,
        s.intent, s.reason, s.emotion, s.scenario, s.llm_cost_usd,
        json.dumps(s.model_calls, ensure_ascii=False),
        json.dumps(s.guardrail_trips, ensure_ascii=False), s.outcome))


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


def close() -> None:
    global _pool
    if _pool is not None:
        try:
            _pool.close()
        except Exception:
            pass
        _pool = None
