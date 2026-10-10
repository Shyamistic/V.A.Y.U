-- Seed the single pilot tenant (design §10.5, ASM-1).
BEGIN;
INSERT INTO tenants (tenant_id, display_name)
VALUES ('t-pilot', 'MonsoonGrid pilot tenant')
ON CONFLICT (tenant_id) DO NOTHING;
COMMIT;
