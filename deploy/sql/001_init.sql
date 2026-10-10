-- MonsoonGrid Postgres schema (design §10.5). Maps the four SQLite tables to
-- Postgres with a NOT NULL tenant FK everywhere, tenant-leading composite PKs,
-- a UNIQUE index on approval_binding_digest, a no-self-approval CHECK, and CHECK
-- enumerations drawn from the contracts.py Literal types. Execution requires a
-- Postgres instance and is out of scope for this task (validated by SQL review).
BEGIN;

CREATE TABLE tenants (
  tenant_id    TEXT PRIMARY KEY CHECK (tenant_id ~ '^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$'),
  display_name TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE certificates (
  tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
  certificate_id  TEXT NOT NULL CHECK (certificate_id ~ '^[A-Za-z0-9_.:-]+$'),
  replay_id       TEXT NOT NULL,
  decision_id     TEXT NOT NULL,
  state           TEXT NOT NULL CHECK (state IN
                    ('ACCEPTED','REJECTED','FALLBACK','ESCALATED','OVERRIDDEN')),
  unsigned_digest CHAR(64) NOT NULL CHECK (unsigned_digest ~ '^[0-9a-f]{64}$'),
  artifact_uri    TEXT,
  artifact_digest CHAR(64) CHECK (artifact_digest ~ '^[0-9a-f]{64}$'),
  manifest        JSONB NOT NULL,
  source_revision TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, certificate_id)
);
CREATE INDEX certificates_by_replay  ON certificates (tenant_id, replay_id, decision_id);
CREATE INDEX certificates_by_created ON certificates (tenant_id, created_at DESC);

CREATE TABLE approvals (
  tenant_id          TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
  approval_id        TEXT NOT NULL,
  certificate_id     TEXT,
  replay_id          TEXT NOT NULL,
  decision_id        TEXT NOT NULL,
  state              TEXT NOT NULL CHECK (state IN
                       ('PENDING','APPROVED','REJECTED','ESCALATED','APPLIED')),
  action             TEXT CHECK (action IN ('approve','reject','override','escalate')),
  actor_id           TEXT NOT NULL CHECK (actor_id ~ '^[A-Za-z0-9_.:-]+$'),
  auth_method        TEXT NOT NULL CHECK (auth_method IN ('google_oidc','local_static_bearer')),
  token_subject      TEXT NOT NULL,
  created_by_subject TEXT,
  created_by_class   TEXT CHECK (created_by_class IN ('human','service_account','unknown')),
  separation_of_duty TEXT NOT NULL CHECK (separation_of_duty IN ('verified_distinct','unverifiable')),
  expected_state_digest   CHAR(64) NOT NULL CHECK (expected_state_digest ~ '^[0-9a-f]{64}$'),
  approval_binding_digest CHAR(64) NOT NULL CHECK (approval_binding_digest ~ '^[0-9a-f]{64}$'),
  reason             TEXT NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 512),
  approval           JSONB NOT NULL,
  recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, approval_id),
  FOREIGN KEY (tenant_id, certificate_id)
    REFERENCES certificates(tenant_id, certificate_id) ON DELETE RESTRICT,
  CONSTRAINT approvals_no_self_approval
    CHECK (created_by_subject IS NULL OR created_by_subject <> token_subject)
);
CREATE UNIQUE INDEX approvals_binding_unique ON approvals (tenant_id, approval_binding_digest);
CREATE INDEX approvals_by_decision ON approvals (tenant_id, replay_id, decision_id);

CREATE TABLE events (
  tenant_id  TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
  event_id   TEXT NOT NULL,
  replay_id  TEXT NOT NULL,
  slot_index SMALLINT CHECK (slot_index BETWEEN 0 AND 95),
  event_type TEXT NOT NULL,
  event      JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, event_id)
);
CREATE INDEX events_by_replay ON events (tenant_id, replay_id, slot_index);

CREATE TABLE idempotency (
  tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
  idempotency_key TEXT NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9_.:-]+$'),
  route           TEXT NOT NULL,
  request_digest  CHAR(64) NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
  response        JSONB NOT NULL,
  actor_id        TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at      TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, idempotency_key)
);
CREATE INDEX idempotency_expiry ON idempotency (expires_at);

COMMIT;
