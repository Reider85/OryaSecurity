-- Add key_fingerprint column for API key lookup
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS key_fingerprint CHAR(64);

-- Create unique index for fast fingerprint lookup (NULLs are skipped in unique indexes)
CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_fingerprint ON api_keys(key_fingerprint);