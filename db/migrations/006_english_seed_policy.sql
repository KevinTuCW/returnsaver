\set ON_ERROR_STOP on
BEGIN;

-- Admin is English-only. Update only the untouched system seed; merchant-created
-- versions are append-only records and must never be rewritten by a migration.
UPDATE rs_merchant_config
SET exceptions_note = 'Quality issues and shipping damage are exempt from the return window and opened-item restrictions.'
WHERE tenant_id = 'public' AND version = 1 AND created_by = 'migration';

UPDATE rs_merchant_rule AS rule
SET text = translated.text
FROM (VALUES
    ('R-WINDOW',  'Returns are accepted within 30 days of delivery.'),
    ('R-FINAL',   'Items marked Final Sale are not eligible for return or exchange.'),
    ('R-HYGIENE', 'Opened intimate apparel and swimwear cannot be returned for hygiene reasons.'),
    ('R-USED',    'Items must be unused and retain all original tags to qualify for a discretionary return.'),
    ('R-DAMAGE',  'Damaged or incorrectly shipped items qualify for replacement or refund regardless of the standard restrictions.')
) AS translated(code, text)
WHERE rule.tenant_id = 'public'
  AND rule.version = 1
  AND rule.code = translated.code
  AND EXISTS (
      SELECT 1 FROM rs_merchant_config AS config
      WHERE config.tenant_id = rule.tenant_id
        AND config.version = rule.version
        AND config.created_by = 'migration');

COMMIT;
