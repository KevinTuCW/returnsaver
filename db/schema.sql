-- Return Saver — PostgreSQL schema
-- 建表：psql "$DATABASE_URL" -f db/schema.sql
--
-- 只持久化"丢了会出事"的三张表：
--   会话（重启不能丢，否则用户要重新确认订单）
--   执行幂等（丢了会重复发钱）
--   人工工单（丢了会违反 SLA 承诺）
-- 订单与客户仍走 mock —— case 明确要求不接真 Shopify。

CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    customer_id     TEXT,
    order_id        TEXT,
    stage           TEXT        NOT NULL,
    round           INT         NOT NULL DEFAULT 0,   -- 已发出的挽留轮次
    turns           INT         NOT NULL DEFAULT 0,   -- 总交互次数
    intent          TEXT,
    reason          TEXT,
    emotion         REAL        NOT NULL DEFAULT 0,
    scenario        TEXT,
    llm_cost_usd    NUMERIC(12,6) NOT NULL DEFAULT 0,
    model_calls     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    guardrail_trips JSONB       NOT NULL DEFAULT '[]'::jsonb,
    outcome         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessions_customer  ON sessions (customer_id);
CREATE INDEX IF NOT EXISTS idx_sessions_created   ON sessions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_scenario  ON sessions (scenario);

-- 幂等表：同一个 idempotency_key 只允许执行一次
CREATE TABLE IF NOT EXISTS executions (
    idempotency_key TEXT PRIMARY KEY,
    order_id        TEXT        NOT NULL,
    offer_id        TEXT        NOT NULL,
    value           NUMERIC(12,2) NOT NULL,
    session_id      TEXT,
    executed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT value_non_negative CHECK (value >= 0)
);

CREATE INDEX IF NOT EXISTS idx_executions_order ON executions (order_id);

-- 人工介入工单
CREATE TABLE IF NOT EXISTS manual_tickets (
    ticket_id   TEXT PRIMARY KEY,
    session_id  TEXT,
    order_id    TEXT        NOT NULL,
    customer_id TEXT,
    priority    TEXT        NOT NULL,              -- P1 | P2
    sla_hours   INT         NOT NULL,
    amount      NUMERIC(12,2) NOT NULL,
    anomalies   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    status      TEXT        NOT NULL DEFAULT 'open', -- open | claimed | resolved
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 不能用生成列：timestamptz + interval 是 STABLE 而非 IMMUTABLE，
    -- 改在 INSERT 时算好（见 db.enqueue_ticket）
    due_at      TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ
);

-- SLA 看板靠这个索引：按到期时间捞还没处理的工单
CREATE INDEX IF NOT EXISTS idx_tickets_open_due
    ON manual_tickets (due_at) WHERE status <> 'resolved';
