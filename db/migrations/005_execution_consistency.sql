-- Tenant-scoped idempotency and one open review per conversation/order.
\set ON_ERROR_STOP on
BEGIN;

-- Prerequisite: 001_multitenant_config.sql. Check before making any changes so
-- a missing base migration cannot leave this migration half-applied.
DO $$
BEGIN
    IF to_regclass('rs_merchant_config') IS NULL THEN
        RAISE EXCEPTION
            'missing rs_merchant_config: run db/migrations/001_multitenant_config.sql before 005';
    END IF;
END $$;

ALTER TABLE executions ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'public';
ALTER TABLE executions ADD COLUMN IF NOT EXISTS token_jti TEXT;
UPDATE executions SET token_jti = idempotency_key WHERE token_jti IS NULL;
ALTER TABLE executions ALTER COLUMN token_jti SET NOT NULL;
ALTER TABLE executions DROP CONSTRAINT IF EXISTS executions_pkey;
ALTER TABLE executions ADD PRIMARY KEY (tenant_id, idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS executions_tenant_token_jti_idx
    ON executions (tenant_id, token_jti);

ALTER TABLE manual_tickets ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'public';

-- Older builds could create multiple open tickets when a customer resumed the
-- same conversation. Preserve the earliest case and its audit trail; close only
-- the later duplicates before adding the invariant.
WITH ranked AS (
    SELECT ctid,
           row_number() OVER (
               PARTITION BY tenant_id, session_id, order_id
               ORDER BY created_at, ticket_id) AS duplicate_no
    FROM manual_tickets
    WHERE status <> 'resolved'
      AND session_id IS NOT NULL
)
UPDATE manual_tickets AS ticket
SET status = 'resolved', resolved_at = COALESCE(ticket.resolved_at, now())
FROM ranked
WHERE ticket.ctid = ranked.ctid
  AND ranked.duplicate_no > 1;

CREATE UNIQUE INDEX IF NOT EXISTS manual_tickets_one_open_case_idx
    ON manual_tickets (tenant_id, session_id, order_id)
    WHERE status <> 'resolved';

CREATE TABLE IF NOT EXISTS rs_policy_extras (
    tenant_id     TEXT  NOT NULL,
    version       INT   NOT NULL,
    special_rules JSONB NOT NULL DEFAULT '[]'::jsonb,
    auto_refund   JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (tenant_id, version),
    FOREIGN KEY (tenant_id, version)
        REFERENCES rs_merchant_config (tenant_id, version) ON DELETE CASCADE
);

COMMIT;
