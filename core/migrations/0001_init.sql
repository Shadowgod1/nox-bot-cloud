-- ILER Bot initial schema (spec section 5.4)

CREATE TABLE users (
  id            bigserial PRIMARY KEY,
  email         text NOT NULL UNIQUE,
  password_hash text NOT NULL,
  name          text NOT NULL DEFAULT '',
  role          text NOT NULL CHECK (role IN ('owner', 'operator')),
  failed_logins int NOT NULL DEFAULT 0,
  locked_until  timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_login_at timestamptz
);

CREATE TABLE settings (
  key        text PRIMARY KEY,
  value      jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE wa_instance (
  id           int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  evo_name     text NOT NULL,
  phone        text,
  status       text NOT NULL DEFAULT 'close',  -- close | connecting | open | degraded
  last_seen_at timestamptz,
  last_error   text,
  send_failures int NOT NULL DEFAULT 0,
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE conversations (
  id              bigserial PRIMARY KEY,
  chat_jid        text NOT NULL UNIQUE,
  phone           text NOT NULL,
  name            text NOT NULL DEFAULT '',
  status          text NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'paused', 'needs_operator', 'closed')),
  paused_until    timestamptz,
  last_message_at timestamptz,
  unread          int NOT NULL DEFAULT 0,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversations_last_message_idx ON conversations (last_message_at DESC);

CREATE TABLE messages (
  id              bigserial PRIMARY KEY,
  conversation_id bigint NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  direction       text NOT NULL CHECK (direction IN ('in', 'out')),
  author          text NOT NULL CHECK (author IN ('client', 'bot', 'operator', 'system')),
  text            text NOT NULL DEFAULT '',
  media           jsonb,
  evo_message_id  text,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX messages_conversation_idx ON messages (conversation_id, id);
CREATE INDEX messages_evo_idx ON messages (evo_message_id) WHERE evo_message_id IS NOT NULL;

CREATE TABLE inbound_events (
  id             bigserial PRIMARY KEY,
  evo_message_id text NOT NULL UNIQUE,
  payload        jsonb NOT NULL,
  status         text NOT NULL DEFAULT 'received'
                 CHECK (status IN ('received', 'processing', 'processed', 'ignored', 'failed')),
  attempts       int NOT NULL DEFAULT 0,
  error          text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX inbound_events_status_idx ON inbound_events (status, created_at);

CREATE TABLE outbound_intents (
  id              bigserial PRIMARY KEY,
  idem_key        text NOT NULL UNIQUE,
  conversation_id bigint REFERENCES conversations(id) ON DELETE CASCADE,
  chat_jid        text NOT NULL,
  text            text NOT NULL,
  status          text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'uncertain')),
  attempts        int NOT NULL DEFAULT 0,
  evo_message_id  text,
  error           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX outbound_intents_status_idx ON outbound_intents (status, updated_at);

CREATE TABLE booking_requests (
  id              bigserial PRIMARY KEY,
  conversation_id bigint NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  payload         jsonb NOT NULL,
  status          text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'confirmed', 'rejected', 'expired')),
  admin_note      text,
  timeout_notified boolean NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now(),
  decided_at      timestamptz,
  decided_by      text
);
CREATE INDEX booking_requests_status_idx ON booking_requests (status, created_at);

CREATE TABLE ai_requests (
  id              bigserial PRIMARY KEY,
  conversation_id bigint REFERENCES conversations(id) ON DELETE SET NULL,
  provider        text NOT NULL,
  model           text NOT NULL,
  input_tokens    int NOT NULL DEFAULT 0,
  output_tokens   int NOT NULL DEFAULT 0,
  latency_ms      int NOT NULL DEFAULT 0,
  error           text,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tool_calls (
  id            bigserial PRIMARY KEY,
  ai_request_id bigint REFERENCES ai_requests(id) ON DELETE CASCADE,
  name          text NOT NULL,
  args          jsonb NOT NULL DEFAULT '{}'::jsonb,
  result        jsonb,
  error         text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
  id         bigserial PRIMARY KEY,
  user_id    bigint REFERENCES users(id) ON DELETE SET NULL,
  action     text NOT NULL,
  details    jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
