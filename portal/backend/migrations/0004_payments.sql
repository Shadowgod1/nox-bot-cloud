-- Free test tariff, invoices issued by a manager, online payments (provider abstraction, mock first).
-- Additive only: accounts already in awaiting_payment with an automatically issued invoice keep working.
--
-- status after verified:
--   free tariff (trial)      -> approved at once (guards: global cap, one trial per e-mail / phone); suspended
--                               after NOX_FREE_DAYS (suspend_reason trial_ended) until a paid tariff is paid
--   paid tariff, by invoice  -> awaiting_invoice   --staff issues the invoice--> awaiting_payment --marked paid--> approved
--   paid tariff, online      -> payment_pending    --verified provider webhook "succeeded"--> approved
-- A Free account that already has a bot upgrades through an "upgrade" invoice; its status does not change.

ALTER TABLE accounts DROP CONSTRAINT accounts_status_check;
ALTER TABLE accounts ADD CONSTRAINT accounts_status_check
  CHECK (status IN ('new','verified','awaiting_invoice','payment_pending','awaiting_payment',
                    'approved','provisioning','active','failed','rejected','suspended','removed'));

ALTER TABLE accounts
  -- how the client chose to pay for the subscription: invoice | online | free (NULL: before this migration)
  ADD COLUMN pay_method text CHECK (pay_method IN ('invoice','online','free')),
  -- optional manager comment printed on the subscription invoice
  ADD COLUMN invoice_note text NOT NULL DEFAULT '',
  -- period_end for which the "time to issue the renewal invoice" notice was sent (once per period)
  ADD COLUMN renewal_notice_for timestamptz,
  -- opaque recurring token from an online provider (never a card number); unused unless the provider gives one
  ADD COLUMN pay_token text,
  ADD COLUMN pay_token_provider text,
  -- Free trial: the account is suspended (suspend_reason trial_ended) at this moment unless upgraded
  ADD COLUMN free_until timestamptz,
  -- a Free account asked to move to this paid tariff "через менеджера" (staff issues the upgrade invoice)
  ADD COLUMN upgrade_tariff text;

-- One Free trial per e-mail and per phone (digits only), ever.
CREATE TABLE free_trials (
  id          bigserial PRIMARY KEY,
  account_id  bigint REFERENCES accounts(id) ON DELETE SET NULL,
  email       text NOT NULL UNIQUE,
  phone       text NOT NULL UNIQUE,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- upgrade: a Free account moving to a paid tariff (paying it starts a paid period with the same bot)
ALTER TABLE invoices DROP CONSTRAINT invoices_kind_check;
ALTER TABLE invoices ADD CONSTRAINT invoices_kind_check CHECK (kind IN ('renewal','topup','upgrade'));

-- renewal / top-up invoices issued by a manager: optional comment and who issued it (NULL: issued automatically)
ALTER TABLE invoices
  ADD COLUMN note text NOT NULL DEFAULT '',
  ADD COLUMN staff_id bigint REFERENCES staff(id);

-- "Пополнить через менеджера": a request the staff answers with a top-up invoice
CREATE TABLE topup_requests (
  id          bigserial PRIMARY KEY,
  account_id  bigint NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  created_at  timestamptz NOT NULL DEFAULT now(),
  invoice_id  bigint REFERENCES invoices(id) ON DELETE SET NULL,
  closed_at   timestamptz
);
CREATE INDEX topup_requests_account_idx ON topup_requests (account_id, created_at DESC);
CREATE UNIQUE INDEX topup_requests_one_open ON topup_requests (account_id) WHERE closed_at IS NULL;

-- One row per online checkout. Card data never reaches the portal: only the provider's payment id.
CREATE TABLE online_payments (
  id                   bigserial PRIMARY KEY,
  account_id           bigint NOT NULL REFERENCES accounts(id),
  purpose              text NOT NULL CHECK (purpose IN ('subscription','topup','renewal','upgrade')),
  amount_kzt           int NOT NULL CHECK (amount_kzt > 0),
  provider             text NOT NULL,
  provider_payment_id  text,
  status               text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','succeeded','failed','refunded')),
  -- what is being paid: tariff + coins (subscription / top-up pack) or an issued invoice (any purpose)
  tariff               text,
  coins                int,
  invoice_no           text,
  redirect_url         text,
  -- the ledger row written when the payment succeeded
  payment_id           bigint REFERENCES payments(id),
  -- sha256 of the last webhook payload applied to this row
  last_event_hash      text,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX online_payments_provider_id ON online_payments (provider, provider_payment_id);
CREATE INDEX online_payments_account_idx ON online_payments (account_id, created_at DESC);

-- Every webhook that passed the signature check: payload hash and a few fields, never the payload itself.
CREATE TABLE payment_events (
  id                   bigserial PRIMARY KEY,
  provider             text NOT NULL,
  provider_payment_id  text NOT NULL,
  online_payment_id    bigint REFERENCES online_payments(id),
  event_id             text NOT NULL DEFAULT '',
  status               text NOT NULL,
  amount_kzt           int,
  payload_sha256       text NOT NULL,
  -- applied | duplicate | unknown_payment | amount_mismatch | not_payable | ignored
  outcome              text NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX payment_events_payment_idx ON payment_events (provider, provider_payment_id, created_at);
CREATE TRIGGER payment_events_no_update BEFORE UPDATE OR DELETE ON payment_events
  FOR EACH ROW EXECUTE FUNCTION ledger_append_only();
CREATE TRIGGER payment_events_no_truncate BEFORE TRUNCATE ON payment_events
  FOR EACH STATEMENT EXECUTE FUNCTION ledger_append_only();
