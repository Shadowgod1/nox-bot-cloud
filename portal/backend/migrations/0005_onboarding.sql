-- Onboarding help and Free trial reminders. Additive only: new nullable columns, existing rows unchanged.

ALTER TABLE accounts
  -- the client pressed "Попросить помочь с настройкой" (a manager connects WhatsApp and fills in services with
  -- them); a repeated press while the request is open sends nothing new
  ADD COLUMN setup_help_requested_at timestamptz,
  -- a manager marked "связались" for the open request (and who did it)
  ADD COLUMN setup_help_contacted_at timestamptz,
  ADD COLUMN setup_help_contacted_by bigint REFERENCES staff(id) ON DELETE SET NULL,
  -- Free trial: the smallest "days before the end" reminder already sent (3, then 1); NULL = none yet
  ADD COLUMN trial_notice_days smallint;

CREATE INDEX accounts_setup_help_open_idx ON accounts (setup_help_requested_at)
  WHERE setup_help_requested_at IS NOT NULL AND setup_help_contacted_at IS NULL;
