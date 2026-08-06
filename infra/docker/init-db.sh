#!/bin/bash
# =============================================================================
# PostgreSQL database initialisation script
# Runs as the postgres superuser during container first-start.
# =============================================================================
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "postgres" <<-EOSQL
    -- Create the main application database (if the entrypoint hasn't already)
    SELECT 'CREATE DATABASE atlascore'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'atlascore')\gexec

    -- Create the test database
    SELECT 'CREATE DATABASE atlascore_test'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'atlascore_test')\gexec

    -- Create the application role (migrations will GRANT specific permissions)
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atlascore') THEN
            CREATE ROLE atlascore WITH LOGIN PASSWORD 'change_in_production'
                NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
        END IF;
    END\$\$;
EOSQL

# Enable extensions in the main database
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "atlascore" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
EOSQL

# Enable extensions in the test database
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "atlascore_test" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
EOSQL

echo "Database initialisation complete."
