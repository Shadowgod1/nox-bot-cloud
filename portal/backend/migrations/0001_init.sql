-- Nox client portal schema.

-- One row per company: the client account and its application are the same thing.
-- status: new (e-mail not verified) -> verified (waiting for staff) -> approved (job queued)
--         -> provisioning (job claimed) -> active | failed; rejected; suspended; removed
CREATE TABLE accounts (
  id               bigserial PRIMARY KEY,
  email            text NOT NULL UNIQUE,
  password_hash    text NOT NULL,
  company          text NOT NULL,
  contact_name     text NOT NULL,
  phone            text NOT NULL,
  slug             text NOT NULL UNIQUE,
  status           text NOT NULL DEFAULT 'new'
                   CHECK (status IN ('new','verified','approved','provisioning','active','failed',
                                     'rejected','suspended','removed')),
  consent_at       timestamptz NOT NULL,
  email_verified_at timestamptz,
  failed_logins    int NOT NULL DEFAULT 0,
  locked_until     timestamptz,
  last_login_at    timestamptz,
  coins_limit      int,
  port             int,
  admin_url        text,
  -- one-time owner setup link of the bot, AES-GCM encrypted; cleared once the client has seen it
  setup_link_enc   text,
  reject_reason    text,
  last_error       text,
  register_ip      text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX accounts_status_idx ON accounts (status, created_at DESC);

-- Nox staff; created only by the CLI (manage.py create-staff).
CREATE TABLE staff (
  id             bigserial PRIMARY KEY,
  email          text NOT NULL UNIQUE,
  name           text NOT NULL DEFAULT '',
  password_hash  text NOT NULL,
  failed_logins  int NOT NULL DEFAULT 0,
  locked_until   timestamptz,
  last_login_at  timestamptz,
  disabled       boolean NOT NULL DEFAULT false,
  created_at     timestamptz NOT NULL DEFAULT now()
);

-- 6-digit codes sent by e-mail; only an HMAC of the code is stored.
CREATE TABLE email_codes (
  id          bigserial PRIMARY KEY,
  account_id  bigint NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  purpose     text NOT NULL CHECK (purpose IN ('verify','reset')),
  code_hash   text NOT NULL,
  attempts    int NOT NULL DEFAULT 0,
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX email_codes_account_idx ON email_codes (account_id, purpose, created_at DESC);

-- Work for the host-side provisioner. At most one open (pending/claimed) job per account.
-- kind: create | suspend | resume | remove; status: pending -> claimed -> done | failed
CREATE TABLE jobs (
  id           bigserial PRIMARY KEY,
  account_id   bigint NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  kind         text NOT NULL CHECK (kind IN ('create','suspend','resume','remove')),
  status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','claimed','done','failed')),
  slug         text NOT NULL,
  coins        int,
  -- per-client Anthropic key sealed with the provisioner's RSA public key (base64); NULL = use the
  -- default key. Wiped when the job succeeds or the account is rejected/removed.
  llm_key_sealed text,
  error        text,
  created_by   bigint REFERENCES staff(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  claimed_at   timestamptz,
  finished_at  timestamptz
);
CREATE UNIQUE INDEX jobs_one_open_per_account ON jobs (account_id) WHERE status IN ('pending','claimed');
CREATE INDEX jobs_pending_idx ON jobs (status, created_at);

CREATE TABLE audit_log (
  id          bigserial PRIMARY KEY,
  staff_id    bigint REFERENCES staff(id),
  account_id  bigint REFERENCES accounts(id) ON DELETE SET NULL,
  action      text NOT NULL,
  details     jsonb NOT NULL DEFAULT '{}'::jsonb,
  ip          text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_created_idx ON audit_log (created_at DESC);
CREATE INDEX audit_log_account_idx ON audit_log (account_id, created_at DESC);

-- Sliding-window rate limits (per IP / per e-mail); old rows are pruned on insert.
CREATE TABLE rate_hits (
  key  text NOT NULL,
  at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX rate_hits_key_idx ON rate_hits (key, at);
