-- Enable pgcrypto for gen_random_uuid() (used by default UUIDs in schema).
CREATE EXTENSION IF NOT EXISTS pgcrypto;
