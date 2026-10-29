# Test Report

**Date:** 2026-10-30
**Environment:** Windows, Python 3.12, Django 5.2.8, PostgreSQL 17.4. The project and CI target
Python 3.13 and PostgreSQL 17.

## Latest full local run

### 1. Default SQLite local development run

Command, with the project's SQLite test settings:

```powershell
$env:DJANGO_SECRET_KEY = 'local-test-secret-not-production'
$env:DJANGO_DEBUG = 'true'
$env:DATABASE_URL = 'sqlite:///:memory:'
$env:DJANGO_ALLOW_DEFAULT_TENANT = 'true'
python -m pytest -q
```

Result: **72 passed, 12 skipped, 0 failed.** The 12 skips are PostgreSQL-specific:
ten in `apps/projects/test_postgres_tenant_isolation.py` and two in
`apps/tenants/test_provisioning.py`. They safely skip when PostgreSQL aliases
are not configured.

Critical controls and targeted tests verified:
- `test_allow_default_tenant_is_rejected_outside_debug_mode`: verifies production fails closed if `DJANGO_ALLOW_DEFAULT_TENANT=true` is set when `DJANGO_DEBUG=false`.
- `test_user_with_change_permission_cannot_delete_another_users_project`: verifies explicit permission separation where users with `projects.change_project` cannot delete non-owned projects.
- `test_user_with_delete_permission_can_delete_another_users_project`: verifies `projects.delete_project` permission enables project deletion.
- `test_project_owner_can_delete_own_project`: verifies project owners retain deletion permissions.
- `test_health_check_returns_ok_when_database_is_healthy`: verifies `/api/health/` returns 200 OK with active database connectivity.
- `test_health_check_returns_503_when_database_fails`: verifies `/api/health/` catches database failures and responds with HTTP 503 without leaking stack traces.
- `test_expire_subscriptions_task_closes_connections`: verifies Celery background task executes `close_old_connections()` in `try/finally`.

### 2. Verified PostgreSQL 17 integration run

Command, against local disposable PostgreSQL 17 test databases:

```powershell
$env:DJANGO_SECRET_KEY = 'ci-only-secret'
$env:DJANGO_DEBUG = 'true'
$env:DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1,testserver'
$env:DATABASE_URL = 'postgresql://app:app@localhost:5432/app'
$env:DJANGO_TENANT_DATABASES = '{"tenant_a":"postgresql://app:app@localhost:5432/tenant_a","tenant_b":"postgresql://app:app@localhost:5432/tenant_b"}'
$env:TENANT_DATABASE_ADMIN_URL = 'postgresql://app:app@localhost:5432/postgres'
$env:TENANT_DOMAIN_SUFFIX = 'localhost'
$env:DJANGO_ALLOW_DEFAULT_TENANT = 'true'
python -m pytest apps/projects/test_postgres_tenant_isolation.py apps/tenants/test_provisioning.py -v --junitxml=test-results.xml
```

Result: **31 passed, 0 skipped, 0 failed.**

All 12 PostgreSQL-specific tests executed and passed live:
1. `test_each_tenant_reads_only_its_own_database` (PASSED)
2. `test_cross_tenant_object_id_cannot_be_read_modified_or_deleted` (PASSED)
3. `test_tenant_write_uses_selected_database` (PASSED)
4. `test_concurrent_project_creation_cannot_exceed_plan_limit` (PASSED)
5. `test_unknown_tenant_host_is_rejected` (PASSED)
6. `test_inactive_tenant_host_is_rejected` (PASSED)
7. `test_user_without_membership_cannot_select_tenant` (PASSED)
8. `test_unconfigured_tenant_database_alias_fails_without_fallback` (PASSED)
9. `test_unavailable_tenant_database_does_not_fall_back` (PASSED)
10. `test_concurrent_tenant_requests_do_not_share_context_or_data` (PASSED)
11. `test_partial_database_creation_can_be_recovered_without_dropping_database` (PASSED)
12. `test_signup_creates_and_routes_to_a_migrated_tenant_database` (PASSED)

## Additional checks

| Check | Result |
|---|---|
| `python -m ruff check .` | Passed (0 lint or style errors) |
| `python -m compileall -q .` | Passed (0 syntax or bytecode errors) |
| `python manage.py check` | Passed (0 issues) |
| `python manage.py makemigrations --check --dry-run` | Passed; no changes detected |
| `docker compose config --quiet` | Passed; syntax only, no services started |
| `git diff --check` | Passed (clean whitespace and line endings) |
| `python manage.py check --deploy` | Passed with explicit production HTTPS/HSTS/proxy settings; zero warnings |
