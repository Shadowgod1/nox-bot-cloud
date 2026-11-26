-- One account for the cabinet and the bot panel. Additive only: existing rows get false and keep the one-time
-- setup link path.

ALTER TABLE accounts
  -- the cloud bot was created with the owner from this application (e-mail, name, the cabinet password hash):
  -- the client logs in to the panel with the cabinet e-mail and password, there is no setup link
  ADD COLUMN owner_preset boolean NOT NULL DEFAULT false;
