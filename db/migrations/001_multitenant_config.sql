-- 001 — 多租户化与配置下沉（非破坏式）。
--
-- 前置：先应用 db/schema.sql（建 sessions / executions / manual_tickets）。
--   psql "$DATABASE_URL" -f db/schema.sql
--   psql "$DATABASE_URL" -f db/migrations/001_multitenant_config.sql
-- 本文件末尾对 sessions 追加两列，表不存在会在那一步显式报错而不是静默跳过。
--
-- 这个库与 helpmate 共享（阵 01）。Return Saver 自己的表统一 rs_ 前缀，
-- 并且只在 rs_order_attrs 上外键引用 helpmate 的 orders——不复制订单本体，
-- 避免两份真相。
--
-- 为什么 rs_merchant_config 是 append-only：会话要能钉住一个版本，否则商家
-- 中途调低让利上限会让 L4 否掉系统自己承诺给客户的方案。(tenant_id, version)
-- 不可变还带来一个红利——配置快照可以永久缓存。

-- ───────────────────────────── 商家策略配置（append-only）
CREATE TABLE IF NOT EXISTS rs_merchant_config (
    tenant_id               TEXT          NOT NULL,
    version                 INT           NOT NULL,
    return_window_days      INT           NOT NULL,
    max_discount_pct        NUMERIC(4,3)  NOT NULL,
    instant_refund_cap_usd  NUMERIC(10,2) NOT NULL,
    manual_sla_hours        INT           NOT NULL,
    emotion_hard_stop       REAL          NOT NULL,
    emotion_large_model     REAL          NOT NULL,
    high_value_order_usd    NUMERIC(10,2) NOT NULL,
    max_negotiation_rounds  INT           NOT NULL,
    max_session_turns       INT           NOT NULL,
    abuse_negotiation_limit INT           NOT NULL,
    exceptions_note         TEXT          NOT NULL,
    created_at              TIMESTAMPTZ   NOT NULL DEFAULT now(),
    created_by              TEXT,
    note                    TEXT,
    PRIMARY KEY (tenant_id, version),
    -- 与 merchant.BOUNDS 同步。读取时 clamp 是纵深防御，能在写入时挡住更好。
    CONSTRAINT cfg_window_range   CHECK (return_window_days BETWEEN 7 AND 365),
    CONSTRAINT cfg_discount_range CHECK (max_discount_pct BETWEEN 0 AND 0.500),
    CONSTRAINT cfg_refund_range   CHECK (instant_refund_cap_usd BETWEEN 0 AND 500),
    CONSTRAINT cfg_sla_range      CHECK (manual_sla_hours BETWEEN 1 AND 48),
    CONSTRAINT cfg_hardstop_range CHECK (emotion_hard_stop BETWEEN 0.50 AND 0.90),
    CONSTRAINT cfg_large_range    CHECK (emotion_large_model BETWEEN 0.20 AND 0.80),
    -- 相等或反过来会让「升大模型」那一支永远命中不到，路由表变成死代码
    CONSTRAINT cfg_emotion_order  CHECK (emotion_large_model < emotion_hard_stop),
    CONSTRAINT cfg_hv_range       CHECK (high_value_order_usd BETWEEN 20 AND 5000),
    CONSTRAINT cfg_rounds_range   CHECK (max_negotiation_rounds BETWEEN 1 AND 3),
    CONSTRAINT cfg_turns_range    CHECK (max_session_turns BETWEEN 4 AND 20),
    CONSTRAINT cfg_abuse_range    CHECK (abuse_negotiation_limit BETWEEN 2 AND 10)
);

-- ───────────────────────────── 规则原文（按版本成组）
CREATE TABLE IF NOT EXISTS rs_merchant_rule (
    tenant_id TEXT    NOT NULL,
    version   INT     NOT NULL,
    code      TEXT    NOT NULL,
    text      TEXT    NOT NULL,
    enabled   BOOLEAN NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, version, code),
    FOREIGN KEY (tenant_id, version)
        REFERENCES rs_merchant_config (tenant_id, version) ON DELETE CASCADE,
    -- 破损与质量豁免是消费者保护底线，不是商家可选项
    CONSTRAINT rule_damage_always_on
        CHECK (code <> 'R-DAMAGE' OR enabled = true),
    CONSTRAINT rule_text_not_blank CHECK (btrim(text) <> '')
);

-- ───────────────────────────── 商业事实（不版本化）
-- 与策略表分开：升套餐不是改策略。混在一起会让审计轨迹读不懂
-- （"v8 出现是因为升级了套餐，不是因为改了规则"）。
CREATE TABLE IF NOT EXISTS rs_tenant (
    tenant_id       TEXT          PRIMARY KEY,
    plan            TEXT          NOT NULL DEFAULT 'pro',
    cost_budget_usd NUMERIC(6,4)  NOT NULL DEFAULT 0.05,
    status          TEXT          NOT NULL DEFAULT 'active',
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT tenant_plan_enum    CHECK (plan IN ('starter', 'pro', 'advanced')),
    CONSTRAINT tenant_status_enum  CHECK (status IN ('active', 'suspended')),
    CONSTRAINT tenant_budget_range CHECK (cost_budget_usd BETWEEN 0.005 AND 0.30)
);

-- ───────────────────────────── 订单属性（helpmate orders 的 1:1 扩展）
-- delivered_at 而不是 days_since_delivery：数据库里存"距今多少天"的整数
-- 第二天就是错的。mock 能蒙过去只因为它从不持久化。
CREATE TABLE IF NOT EXISTS rs_order_attrs (
    order_id              TEXT    PRIMARY KEY
        REFERENCES orders (order_id) ON DELETE CASCADE,
    sku                   TEXT,
    -- 商品展示名。helpmate 的 orders 没有这一列（它只有 customer 姓名），而挽留
    -- 话术要说出商品名（"Merino Crew Tee 穿起来偏小"）。属于本服务的扩展字段，
    -- 所以放这里，而不是去挪用 orders.customer。
    product               TEXT,
    category              TEXT,
    gross_margin_pct      REAL,
    delivered_at          TIMESTAMPTZ,
    final_sale            BOOLEAN NOT NULL DEFAULT false,
    opened                BOOLEAN NOT NULL DEFAULT false,
    used                  BOOLEAN NOT NULL DEFAULT false,
    sizes_in_stock        TEXT[]  NOT NULL DEFAULT '{}',
    repairable            BOOLEAN NOT NULL DEFAULT false,
    negotiations_last_90d INT     NOT NULL DEFAULT 0,
    has_manual            BOOLEAN NOT NULL DEFAULT false
);

-- 对已建表的库补列（CREATE TABLE IF NOT EXISTS 不会改已有表结构）
ALTER TABLE rs_order_attrs ADD COLUMN IF NOT EXISTS product TEXT;

-- ───────────────────────────── 客户属性（helpmate 没有客户表）
-- 不设到 orders 的外键：客户属性的生命周期比单个订单长，且 helpmate 侧
-- 只在 orders 上存 customer_id 字符串，没有可指向的客户主键。
CREATE TABLE IF NOT EXISTS rs_customer_attrs (
    tenant_id        TEXT    NOT NULL,
    customer_id      TEXT    NOT NULL,
    tier             TEXT    NOT NULL DEFAULT 'normal',
    lifetime_orders  INT     NOT NULL DEFAULT 0,
    returns_last_90d INT     NOT NULL DEFAULT 0,
    risk_flag        BOOLEAN NOT NULL DEFAULT false,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, customer_id),
    CONSTRAINT customer_tier_enum CHECK (tier IN ('normal', 'vip'))
);

-- ───────────────────────────── seed：把今天的单商户配置变成 public 的 v1
INSERT INTO rs_tenant (tenant_id, plan, cost_budget_usd)
VALUES ('public', 'pro', 0.05)
ON CONFLICT (tenant_id) DO NOTHING;

INSERT INTO rs_merchant_config (
    tenant_id, version, return_window_days, max_discount_pct,
    instant_refund_cap_usd, manual_sla_hours, emotion_hard_stop,
    emotion_large_model, high_value_order_usd, max_negotiation_rounds,
    max_session_turns, abuse_negotiation_limit, exceptions_note,
    created_by, note)
VALUES ('public', 1, 30, 0.300, 50.00, 2, 0.70, 0.45, 200.00, 2, 8, 3,
        '质量问题与物流破损不受窗口与拆封限制，随时受理。',
        'migration', '从 store.MERCHANT_POLICY 迁移')
ON CONFLICT (tenant_id, version) DO NOTHING;

INSERT INTO rs_merchant_rule (tenant_id, version, code, text) VALUES
  ('public', 1, 'R-WINDOW',  '自签收之日起 30 天内可申请退货，逾期不再受理。'),
  ('public', 1, 'R-FINAL',   '标记为 Final Sale 的清仓商品不支持退换。'),
  ('public', 1, 'R-HYGIENE', '内衣、泳装等贴身类目一经拆封，出于卫生考虑不支持退货。'),
  ('public', 1, 'R-USED',    '商品需保持未使用、吊牌完整状态；已明显使用的不支持无理由退货。'),
  ('public', 1, 'R-DAMAGE',  '商品到货破损或发错，不受上述限制，可直接换货或退款。')
ON CONFLICT (tenant_id, version, code) DO NOTHING;

-- ───────────────────────────── sessions 追加租户与钉住的版本
-- 放在最后：sessions 由 db/schema.sql 建，本文件不重复定义它（两份定义迟早
-- 漂移）。表不存在时下面这句会显式失败，提示先跑 schema.sql——比静默跳过好。
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                   WHERE table_name = 'sessions') THEN
        RAISE EXCEPTION '缺少 sessions 表：请先执行 psql "$DATABASE_URL" -f db/schema.sql';
    END IF;
END $$;

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS tenant_id      TEXT NOT NULL DEFAULT 'public';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS config_version INT  NOT NULL DEFAULT 0;
-- C2（Dashboard 按租户按日聚合）要靠这个索引
CREATE INDEX IF NOT EXISTS sessions_tenant_idx ON sessions (tenant_id, created_at DESC);
