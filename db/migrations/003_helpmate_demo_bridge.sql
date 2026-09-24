-- 003 — demo data for the helpmate bridge. Do not apply in production.
--
-- Apply with:
--   psql "$DATABASE_URL" -f db/migrations/003_helpmate_demo_bridge.sql

INSERT INTO rs_order_attrs (order_id, sku, product, category, gross_margin_pct,
    delivered_at, final_sale, opened, used, sizes_in_stock, repairable,
    negotiations_last_90d, has_manual) VALUES
  ('A1001', 'TEE-BLK-M', 'Merino Crew Tee（黑 / M）', 'apparel', 0.60,
   now() - interval '5 days', false, true, false, '{S,L,XL}', false, 0, true),
  ('A1002', 'MUG-CER-01', '陶瓷马克杯', 'home', 0.55,
   now() - interval '2 days', false, true, false, '{}', false, 0, false)
ON CONFLICT (order_id) DO NOTHING;

INSERT INTO rs_customer_attrs (tenant_id, customer_id, tier, lifetime_orders,
                               returns_last_90d, risk_flag) VALUES
  ('public', 'Alice', 'vip',     9, 0, false),
  ('public', 'Bob',   'normal',  2, 0, false)
ON CONFLICT (tenant_id, customer_id) DO NOTHING;
