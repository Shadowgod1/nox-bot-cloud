-- Client card: a short memory about a client that the bot sees in every reply (engine.client_memory).
--
-- One row per client. `contact` is the same key as known_contacts: the phone digits, or the chat JID when
-- WhatsApp hides the phone (reply_rules.contact_key). Booking requests are not copied here: the card reads
-- them from booking_requests. Additive: live installs simply get an empty table.
CREATE TABLE IF NOT EXISTS client_profiles (
  contact    text PRIMARY KEY,
  name       text NOT NULL DEFAULT '',  -- the name the client gave themselves (e.g. in a booking request)
  notes      text NOT NULL DEFAULT '',  -- markdown, one "- fact" per line, at most ~800 characters
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- The card shows the client's last booking requests: by conversation, newest first.
CREATE INDEX IF NOT EXISTS booking_requests_conversation_idx ON booking_requests (conversation_id, id DESC);
