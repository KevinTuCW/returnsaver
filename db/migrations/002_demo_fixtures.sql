-- 002 — 演示数据（仅用于本地演示与 B 阶段联调，**生产不执行**）。
--
-- 应用：psql "$DATABASE_URL" -f db/migrations/002_demo_fixtures.sql
--
-- ⚠️ 这个文件里 Return Saver 的迁移**写 helpmate 的 orders 表**，是共享库
-- 方案的直接代价。原因：rs_order_attrs 外键指向 orders，而 helpmate 的
-- db/seed.sql 只有 A1001 / A1002 两个订单（客户 Alice / Bob）。Return Saver
-- 的五个场景需要 8 个订单（ORD-1001..ORD-1008，客户 C-001..C-007），它们在
-- helpmate 库里并不存在——直接 seed rs_order_attrs 会当场外键失败。
--
-- 所以它单独成文件、明确标注、且生产不跑：生产的订单由 helpmate 侧的真实业务
-- 写入，Return Saver 只补 rs_order_attrs。
--
-- 全部 ON CONFLICT DO NOTHING：可重复执行，且不会覆盖 helpmate 已有的
-- A1001 / A1002。
--
-- 注意 orders.customer 存的是**客户姓名**（helpmate 的语义），商品展示名在
-- rs_order_attrs.product——不要把商品名塞进 customer 列。

-- ───────────────────────────── 演示订单（写 helpmate 的表）
INSERT INTO orders (order_id, tenant_id, customer_id, customer, status, total) VALUES
  ('ORD-1001', 'public', 'C-001', 'Sarah', 'delivered', 100.00),
  ('ORD-1002', 'public', 'C-002', 'Mike',  'delivered',  38.00),
  ('ORD-1003', 'public', 'C-003', 'Elena', 'delivered', 420.00),
  ('ORD-1004', 'public', 'C-004', 'Raj',   'delivered', 260.00),
  ('ORD-1005', 'public', 'C-005', 'Nina',  'delivered',  45.00),
  ('ORD-1006', 'public', 'C-006', 'Tom',   'delivered', 680.00),
  ('ORD-1007', 'public', 'C-007', 'Alex',  'delivered',  90.00),
  ('ORD-1008', 'public', 'C-007', 'Alex',  'delivered', 100.00)
ON CONFLICT (order_id) DO NOTHING;

-- ───────────────────────────── 订单属性
-- delivered_at 由 days_since_delivery 反算，让演示订单相对"今天"永远正确
INSERT INTO rs_order_attrs (order_id, sku, product, category, gross_margin_pct,
    delivered_at, final_sale, opened, used, sizes_in_stock, repairable,
    negotiations_last_90d, has_manual) VALUES
  ('ORD-1001','TEE-BLK-M',  'Merino Crew Tee (Black / M)','apparel',    0.60, now() - interval  '6 days', false,true,false, '{S,L,XL}', false,0,true),
  ('ORD-1002','MUG-CER-01', 'Ceramic Mug',                'home',       0.55, now() - interval  '2 days', false,true,false, '{}',       false,0,false),
  ('ORD-1003','JKT-DWN-L',  'Down Jacket (L)',            'apparel',    0.58, now() - interval '41 days', false,true,true,  '{M,L}',    true, 0,true),
  ('ORD-1004','HDP-ANC-02', 'ANC Headphones Gen2',        'electronics',0.45, now() - interval  '5 days', false,true,true,  '{}',       true, 0,true),
  ('ORD-1005','LMP-DSK-03', 'Desk Lamp',                  'home',       0.50, now() - interval  '3 days', false,true,true,  '{}',       true, 0,true),
  ('ORD-1006','CAM-MRL-01', 'Mirrorless Camera Body',     'electronics',0.32, now() - interval  '4 days', false,true,true,  '{}',       true, 0,true),
  ('ORD-1007','SNK-RUN-42', 'Runner Sneakers (42)',       'footwear',   0.52, now() - interval  '7 days', true, true,true,  '{41,43}',  false,4,false),
  ('ORD-1008','TEE-BLK-M',  'Merino Crew Tee (Black / S)','apparel',    0.60, now() - interval  '5 days', false,true,false, '{M,L}',    false,4,true)
ON CONFLICT (order_id) DO NOTHING;

-- ───────────────────────────── 客户属性
INSERT INTO rs_customer_attrs (tenant_id, customer_id, tier, lifetime_orders,
                               returns_last_90d, risk_flag) VALUES
  ('public','C-001','vip',    11, 0, false),
  ('public','C-002','normal',  2, 0, false),
  ('public','C-003','normal',  4, 1, false),
  ('public','C-004','normal',  1, 0, false),
  ('public','C-005','normal',  3, 0, false),
  ('public','C-006','vip',     8, 0, false),
  ('public','C-007','normal', 20, 6, true)     -- 薅羊毛：风险标记 + 90 天 6 次退货
ON CONFLICT (tenant_id, customer_id) DO NOTHING;
