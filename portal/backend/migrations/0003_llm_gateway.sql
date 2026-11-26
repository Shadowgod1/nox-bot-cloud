-- LLM gateway: virtual keys, coin ledgers (credits and spend), billing periods, renewal and top-up invoices.
--
-- Balance of an account = monthly grant of the current period + top-up coins carried from earlier periods
--   + top-ups / adjustments since counted_since - spend since counted_since.
-- At a period rollover unused monthly coins expire; top-up coins carry over (accounts.topup_carry).

-- generic guard for ledgers: rows are never changed or deleted
CREATE FUNCTION ledger_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: add a correcting row instead of %', TG_TABLE_NAME, TG_OP;
END $$;

ALTER TABLE accounts
  -- installed on the client's own machine: no provisioning jobs, the technician sets the key by hand
  ADD COLUMN local_install   boolean NOT NULL DEFAULT false,
  -- monthly billing period (from activation); NULL = the account does not use the gateway
  ADD COLUMN period_start    timestamptz,
  ADD COLUMN period_end      timestamptz,
  -- ledger rows created at or after this moment belong to the current period (set at each rollover)
  ADD COLUMN counted_since   timestamptz,
  -- top-up coins left over from earlier periods
  ADD COLUMN topup_carry     numeric(14,3) NOT NULL DEFAULT 0,
  -- lowest "coins left" warning (percent) already e-mailed in this period; 100 = none yet
  ADD COLUMN low_balance_sent int NOT NULL DEFAULT 100,
  -- why the account is suspended: unpaid (renewal overdue) or NULL (staff action)
  ADD COLUMN suspend_reason  text;

-- Virtual keys nox_vk_<random>: only the sha256 is stored; the full key is shown once.
CREATE TABLE virtual_keys (
  id            bigserial PRIMARY KEY,
  account_id    bigint NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  key_hash      text NOT NULL UNIQUE,
  prefix        text NOT NULL,
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
  created_by    bigint REFERENCES staff(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_used_at  timestamptz,
  revoked_at    timestamptz,
  revoke_reason text
);
CREATE INDEX virtual_keys_account_idx ON virtual_keys (account_id, created_at DESC);

-- rekey: rewrite the instance .env with a new virtual key. key_id: the key the job deploys.
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN ('create','suspend','resume','remove','rekey'));
ALTER TABLE jobs
  -- the virtual key sealed for the provisioner like llm_key_sealed; wiped when the job succeeds
  ADD COLUMN gateway_key_sealed text,
  ADD COLUMN key_id bigint REFERENCES virtual_keys(id) ON DELETE SET NULL;

-- Credits: monthly grant at each period start, paid top-ups, staff adjustments. Append-only.
CREATE TABLE coin_grants (
  id            bigserial PRIMARY KEY,
  account_id    bigint NOT NULL REFERENCES accounts(id),
  kind          text NOT NULL CHECK (kind IN ('monthly','topup','adjust')),
  coins         numeric(14,3) NOT NULL CHECK (coins <> 0),
  -- monthly: the period the grant belongs to
  period_start  timestamptz,
  invoice_no    text,
  payment_id    bigint REFERENCES payments(id),
  staff_id      bigint REFERENCES staff(id),
  note          text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX coin_grants_account_idx ON coin_grants (account_id, created_at);
CREATE UNIQUE INDEX coin_grants_one_monthly ON coin_grants (account_id, period_start) WHERE kind = 'monthly';
CREATE TRIGGER coin_grants_no_update BEFORE UPDATE OR DELETE ON coin_grants
  FOR EACH ROW EXECUTE FUNCTION ledger_append_only();
CREATE TRIGGER coin_grants_no_truncate BEFORE TRUNCATE ON coin_grants
  FOR EACH STATEMENT EXECUTE FUNCTION ledger_append_only();

-- Spend: one row per metered upstream response. Append-only.
CREATE TABLE llm_usage (
  id                  bigserial PRIMARY KEY,
  account_id          bigint NOT NULL REFERENCES accounts(id),
  key_id              bigint REFERENCES virtual_keys(id),
  provider            text NOT NULL CHECK (provider IN ('anthropic','openai','gemini')),
  model               text NOT NULL,
  endpoint            text NOT NULL DEFAULT '',
  input_tokens        bigint NOT NULL DEFAULT 0,
  output_tokens       bigint NOT NULL DEFAULT 0,
  cache_write_tokens  bigint NOT NULL DEFAULT 0,
  cache_read_tokens   bigint NOT NULL DEFAULT 0,
  coins               numeric(14,3) NOT NULL CHECK (coins >= 0),
  -- false: model not in the price table (billed at the provider's highest rate) or usage missing (estimate)
  priced              boolean NOT NULL DEFAULT true,
  status              int,
  request_id          text NOT NULL UNIQUE,
  upstream_request_id text,
  created_at          timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX llm_usage_account_idx ON llm_usage (account_id, created_at);
CREATE INDEX llm_usage_created_idx ON llm_usage (created_at);
CREATE TRIGGER llm_usage_no_update BEFORE UPDATE OR DELETE ON llm_usage
  FOR EACH ROW EXECUTE FUNCTION ledger_append_only();
CREATE TRIGGER llm_usage_no_truncate BEFORE TRUNCATE ON llm_usage
  FOR EACH STATEMENT EXECUTE FUNCTION ledger_append_only();

-- In-flight requests: coins reserved until the response is settled (stale rows are ignored and pruned).
CREATE UNLOGGED TABLE llm_reservations (
  id          bigserial PRIMARY KEY,
  account_id  bigint NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  coins       numeric(14,3) NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX llm_reservations_account_idx ON llm_reservations (account_id);

-- Invoices after the first one (which stays on accounts.invoice_no): monthly renewals and top-up packs.
CREATE TABLE invoices (
  id            bigserial PRIMARY KEY,
  account_id    bigint NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  number        text NOT NULL UNIQUE,
  kind          text NOT NULL CHECK (kind IN ('renewal','topup')),
  tariff        text NOT NULL,
  amount_kzt    int NOT NULL CHECK (amount_kzt > 0),
  coins         int NOT NULL CHECK (coins >= 0),
  -- renewal: the period it pays for (starts at the current period_end)
  period_start  timestamptz,
  issued_at     timestamptz NOT NULL DEFAULT now(),
  paid_at       timestamptz,
  payment_id    bigint REFERENCES payments(id)
);
CREATE INDEX invoices_account_idx ON invoices (account_id, issued_at DESC);
CREATE UNIQUE INDEX invoices_one_renewal ON invoices (account_id, period_start) WHERE kind = 'renewal';

-- Small key/value state of background jobs (daily summary date, alert cool-downs).
CREATE TABLE portal_state (
  key        text PRIMARY KEY,
  value      jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
