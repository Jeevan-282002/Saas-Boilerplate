import logging
import re
import threading
import time
from copy import deepcopy

import psycopg
from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.db.utils import OperationalError as DjangoOperationalError
from psycopg import sql

TENANT_DATABASE_ALIAS_PATTERN = re.compile(r"tenant_[0-9a-f]{32}\Z")
_database_registration_lock = threading.RLock()
PROVISIONING_MAX_ATTEMPTS = 3
PROVISIONING_RETRY_DELAY_SECONDS = 0.1
TRANSIENT_POSTGRES_SQLSTATES = {"53300", "57P01", "57P02", "57P03"}
logger = logging.getLogger(__name__)


def _is_transient_connection_error(error):
    current = error
    while current is not None:
        if isinstance(current, (psycopg.OperationalError, DjangoOperationalError)):
            sqlstate = getattr(current, "sqlstate", None)
            if sqlstate is not None:
                return sqlstate.startswith("08") or sqlstate in TRANSIENT_POSTGRES_SQLSTATES
            cause = current.__cause__ or current.__context__
            if cause is None:
                return True
            current = cause
        else:
            current = current.__cause__ or current.__context__
    return False


def register_tenant_database(alias):
    if alias in connections.databases:
        database_config = connections.databases[alias]
        if TENANT_DATABASE_ALIAS_PATTERN.fullmatch(alias) and (
            database_config["ENGINE"] != "django.db.backends.postgresql"
            or database_config["NAME"] != alias
        ):
            raise RuntimeError("Tenant database alias has an unexpected database configuration.")
        return alias
    if not TENANT_DATABASE_ALIAS_PATTERN.fullmatch(alias):
        raise RuntimeError("Tenant database alias is not a valid generated alias.")
    default_database = settings.DATABASES["default"]
    if default_database["ENGINE"] != "django.db.backends.postgresql":
        raise RuntimeError("Tenant database provisioning requires PostgreSQL.")

    with _database_registration_lock:
        if alias not in connections.databases:
            database_config = deepcopy(default_database)
            database_config["NAME"] = alias
            connections.databases[alias] = database_config
    return alias


def provision_tenant_database(tenant):
    alias = tenant.database_alias
    if not TENANT_DATABASE_ALIAS_PATTERN.fullmatch(alias):
        raise RuntimeError("Tenant database alias is not a valid generated alias.")
    admin_url = settings.TENANT_DATABASE_ADMIN_URL
    if not admin_url:
        raise RuntimeError("TENANT_DATABASE_ADMIN_URL is not configured.")
    owner = settings.DATABASES["default"].get("USER")
    if not owner:
        raise RuntimeError("The default database user must be configured as tenant database owner.")
    register_tenant_database(alias)

    for attempt in range(PROVISIONING_MAX_ATTEMPTS):
        try:
            _provision_tenant_database_once(alias, owner, admin_url)
            return
        except Exception as exc:
            if not _is_transient_connection_error(exc) or attempt == PROVISIONING_MAX_ATTEMPTS - 1:
                raise
            logger.warning(
                "Transient tenant provisioning connection failure for alias %s "
                "(attempt %s of %s; %s; SQLSTATE %s).",
                alias,
                attempt + 1,
                PROVISIONING_MAX_ATTEMPTS,
                type(exc).__name__,
                getattr(exc, "sqlstate", None),
            )
            connections[alias].close()
            time.sleep(PROVISIONING_RETRY_DELAY_SECONDS * (2**attempt))


def _provision_tenant_database_once(alias, owner, admin_url):
    stage = "connecting to the provisioning database"
    try:
        with psycopg.connect(admin_url, autocommit=True) as admin_connection:
            stage = "acquiring the tenant database lock"
            admin_connection.execute("SELECT pg_advisory_lock(hashtext(%s))", (alias,))
            try:
                stage = "verifying or creating the tenant database"
                existing_owner = admin_connection.execute(
                    """
                    SELECT pg_catalog.pg_get_userbyid(datdba)
                    FROM pg_catalog.pg_database
                    WHERE datname = %s
                    """,
                    (alias,),
                ).fetchone()
                if existing_owner:
                    if existing_owner[0] != owner:
                        raise RuntimeError("Existing tenant database has an unexpected owner.")
                else:
                    admin_connection.execute(
                        sql.SQL("CREATE DATABASE {} OWNER {}").format(
                            sql.Identifier(alias),
                            sql.Identifier(owner),
                        )
                    )

                stage = "running tenant migrations"
                call_command("migrate", database=alias, interactive=False, verbosity=0)
            finally:
                admin_connection.execute("SELECT pg_advisory_unlock(hashtext(%s))", (alias,))
    except Exception as exc:
        logger.error(
            "Tenant database provisioning failed while %s for alias %s (%s; SQLSTATE %s).",
            stage,
            alias,
            type(exc).__name__,
            getattr(exc, "sqlstate", None),
        )
        raise
