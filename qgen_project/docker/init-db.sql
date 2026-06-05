-- Runs once when the postgres container is first created.
-- Enables the pgvector extension required by QGen.
CREATE EXTENSION IF NOT EXISTS vector;
