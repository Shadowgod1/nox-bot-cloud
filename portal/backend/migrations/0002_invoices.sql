-- Payment by invoice: tariff choice after e-mail verification, invoice, append-only payments ledger.
-- status gains awaiting_payment: verified --choose tariff--> awaiting_payment --staff marks paid--> approved

ALTER TABLE accounts DROP CONSTRAINT accounts_status_check;
ALTER TABLE accounts ADD CONSTRAINT accounts_status_check
  CHECK (status IN ('new','verified','awaiting_payment','approved','provisioning','active','failed',
                    'rejected','suspended','removed'));

ALTER TABLE accounts
  ADD COLUMN tariff            text,
  ADD COLUMN invoice_no        text UNIQUE,
  ADD COLUMN invoice_amount    int,
  ADD COLUMN invoice_issued_at timestamptz;

CREATE SEQUENCE invoice_seq;

-- Ledger: one row per received payment. Rows are never changed or deleted; a correction is a new row
-- (negative amount for a refund or a mistake).
CREATE TABLE payments (
  id          bigserial PRIMARY KEY,
  account_id  bigint NOT NULL REFERENCES accounts(id),
  invoice_no  text,
  tariff      text,
  amount_kzt  int NOT NULL CHECK (amount_kzt <> 0),
  method      text NOT NULL DEFAULT 'invoice',
  reference   text NOT NULL DEFAULT '',
  note        text NOT NULL DEFAULT '',
  staff_id    bigint REFERENCES staff(id),
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX payments_account_idx ON payments (account_id, created_at DESC);

CREATE FUNCTION payments_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'payments is append-only: add a correcting row instead of %', TG_OP;
END $$;

CREATE TRIGGER payments_no_update BEFORE UPDATE OR DELETE ON payments
  FOR EACH ROW EXECUTE FUNCTION payments_append_only();
CREATE TRIGGER payments_no_truncate BEFORE TRUNCATE ON payments
  FOR EACH STATEMENT EXECUTE FUNCTION payments_append_only();
