DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS age;
EXCEPTION
    WHEN undefined_file THEN RAISE NOTICE 'Apache AGE extension is not installed in this image';
END $$;

DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS timescaledb;
EXCEPTION
    WHEN undefined_file THEN RAISE NOTICE 'TimescaleDB extension is not installed in this image';
END $$;

DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION
    WHEN undefined_file THEN RAISE NOTICE 'pgvector extension is not installed in this image';
END $$;
