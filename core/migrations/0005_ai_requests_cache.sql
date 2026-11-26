-- Anthropic prompt caching: the part of input_tokens read from / written to the cache (billed at their own
-- rates by usage.coins_for). input_tokens keeps the total, so older rows (0 / 0) are billed as before.
ALTER TABLE ai_requests ADD COLUMN IF NOT EXISTS cache_read_tokens int NOT NULL DEFAULT 0;
ALTER TABLE ai_requests ADD COLUMN IF NOT EXISTS cache_write_tokens int NOT NULL DEFAULT 0;
