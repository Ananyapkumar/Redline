-- Runs once, on the first start of an empty database.
-- If you change this file later, it will NOT re-run until the volume is destroyed:
--   docker compose down -v && docker compose up -d

-- Vector similarity search, used for clause retrieval.
CREATE EXTENSION IF NOT EXISTS vector;

-- Trigram matching. Supports fuzzy lookup of clause identifiers and policy titles,
-- and backs the keyword half of hybrid retrieval alongside Postgres full-text search.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
