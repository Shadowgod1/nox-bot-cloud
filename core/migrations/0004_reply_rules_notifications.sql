-- Reply rules and lead notifications.
--
-- known_contacts: chats that existed on the WhatsApp number before the bot was linked (history sync on
-- linking, messages sent before the link time, chats the phone owner started). With the reply rule
-- "only new contacts" the bot stays silent for them. `contact` is the phone (digits) or, when WhatsApp
-- hides it, the chat JID.
CREATE TABLE IF NOT EXISTS known_contacts (
  contact    text PRIMARY KEY,
  source     text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- When the current WhatsApp link was made (first `open` after a logout / a fresh install).
ALTER TABLE wa_instance ADD COLUMN IF NOT EXISTS linked_at timestamptz;

-- Notifications sent outside WhatsApp (Telegram): one row per idempotency key, so a repeated call
-- (retried turn, recovery) never notifies twice.
CREATE TABLE IF NOT EXISTS notification_log (
  key        text PRIMARY KEY,
  channel    text NOT NULL,
  status     text NOT NULL,
  error      text,
  created_at timestamptz NOT NULL DEFAULT now()
);
