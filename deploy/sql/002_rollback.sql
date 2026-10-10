-- Reverses 001_init.sql EXACTLY, dropping in reverse foreign-key order
-- (design §10.5). DESTRUCTIVE: requires the same -IUnderstandDataLoss discipline
-- as teardown. Rollback procedure (docs/deployment.md): take a Cloud SQL
-- on-demand backup, apply this, redeploy the previous SQLite-speaking revision,
-- and restore from backup if Postgres-only rows were already written.
BEGIN;

DROP INDEX IF EXISTS idempotency_expiry;
DROP TABLE IF EXISTS idempotency;

DROP INDEX IF EXISTS events_by_replay;
DROP TABLE IF EXISTS events;

DROP INDEX IF EXISTS approvals_by_decision;
DROP INDEX IF EXISTS approvals_binding_unique;
DROP TABLE IF EXISTS approvals;

DROP INDEX IF EXISTS certificates_by_created;
DROP INDEX IF EXISTS certificates_by_replay;
DROP TABLE IF EXISTS certificates;

DROP TABLE IF EXISTS tenants;

COMMIT;
