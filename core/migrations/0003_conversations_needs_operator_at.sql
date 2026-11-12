-- When a conversation last got status needs_operator (any reason: handover tool, LLM failure, coin
-- limit); the dashboard counts today's handovers from it.

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS needs_operator_at timestamptz;
