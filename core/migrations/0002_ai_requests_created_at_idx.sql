-- Monthly coin usage and the dashboard scan ai_requests by created_at.

CREATE INDEX IF NOT EXISTS ai_requests_created_at_idx ON ai_requests (created_at);
