-- Staging PostgreSQL 17 Database Initialization Script
-- Run this script as the 'postgres' superuser on your staging PostgreSQL instance:
-- psql -h <db_host> -U postgres -f scripts/init_staging_db.sql

\set ON_ERROR_STOP on

BEGIN;

-- 1. Create runtime application role (least privilege: LOGIN only, no CREATEDB or SUPERUSER)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_runtime') THEN
        CREATE ROLE app_runtime WITH LOGIN PASSWORD 'STAGING_APP_PASSWORD_HERE';
    END IF;
END
$$;

-- 2. Create tenant provisioning role (LOGIN and CREATEDB)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'tenant_provisioner') THEN
        CREATE ROLE tenant_provisioner WITH LOGIN CREATEDB PASSWORD 'STAGING_PROVISIONER_PASSWORD_HERE';
    END IF;
END
$$;

-- 3. Grant app_runtime role membership to tenant_provisioner
-- In PostgreSQL 16+, a role must be a member of the target role to create databases owned by it.
GRANT app_runtime TO tenant_provisioner;

COMMIT;

-- 4. Create central application database owned by app_runtime (outside transaction block)
SELECT 'CREATE DATABASE app_staging WITH OWNER app_runtime'
WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_database WHERE datname = 'app_staging')\gexec

GRANT ALL PRIVILEGES ON DATABASE app_staging TO app_runtime;

