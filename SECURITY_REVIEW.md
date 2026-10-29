# Security Review

**Date:** 2026-10-30
**Scope:** Fast targeted review of production readiness, Django tenant middleware, JWT
authentication, database router, project and subscription permissions, tenant
provisioning, settings, Celery task, Docker/Compose, Nginx, and CI workflow.
Runtime authorization checks were verified on SQLite and live on PostgreSQL 17.
All 12 PostgreSQL-specific tenant-isolation and provisioning integration tests
were executed against a real PostgreSQL 17 instance and passed.

## Findings

| # | Severity | File | Lines | Vulnerability | Confidence |
|---|----------|------|-------|---------------|------------|
| 1 | 🟠 HIGH | [config/settings.py](./config/settings.py) | 13-32 | Debug defaulted on and missing secret configuration selected a known development signing key, enabling JWT forgery if deployed with defaults. Fixed by defaulting debug off and requiring a strong, non-placeholder key. | 9/10 |
| 2 | 🟡 MEDIUM | [apps/projects/views.py](./apps/projects/views.py), [apps/projects/permissions.py](./apps/projects/permissions.py) | 24-32; 6-16 | Project APIs allowed requests without organization context; creation bypassed subscription and quota enforcement, while default-database fallback could expose unscoped project data. Fixed by requiring an active, ready tenant for every project action. | 9/10 |
| 3 | 🟡 MEDIUM | [apps/projects/views.py](./apps/projects/views.py), [apps/subscriptions/services.py](./apps/subscriptions/services.py) | 24-31; 12-18 | A project write could check an active subscription concurrently with cancellation and commit after that stale entitlement check. Fixed by locking the central subscription row during project writes. | 8/10 |
| 4 | 🟡 MEDIUM | [apps/projects/permissions.py](./apps/projects/permissions.py) | 17-21 | Non-owner DELETE operations permitted users with only `projects.change_project` permission to delete projects. Fixed by enforcing explicit `projects.delete_project` permission (or project owner) for DELETE requests. | 9/10 |
| 5 | 🟡 MEDIUM | [apps/projects/views.py](./apps/projects/views.py) | 24-33 | Project creation wrapped the central subscription lock in `transaction.atomic(using="default")`, but the subsequent insert to the tenant database was uncoordinated. Fixed by enclosing both `default` and the tenant database in dual atomic transactions so tenant record commitment precedes subscription lock release. | 9/10 |
| 6 | 🟢 LOW | [apps/tenants/views.py](./apps/tenants/views.py) | 39-50 | Health check did not perform an active roundtrip probe and raised unhandled 500 exceptions upon database connectivity failures. Fixed by probing `SELECT 1` in a `try/except` block and responding with structured HTTP 503 JSON without leaking internal database errors. | 9/10 |
| 7 | 🟢 LOW | [apps/subscriptions/tasks.py](./apps/subscriptions/tasks.py) | 12-21 | Celery worker processes could reuse stale database connections across background task executions. Fixed by adding `close_old_connections()` in a `try/finally` block. | 8/10 |
| 8 | 🟡 MEDIUM | [config/settings.py](./config/settings.py) | 114-116 | Accidental deployment with `DJANGO_ALLOW_DEFAULT_TENANT=true` would allow unregistered hostnames to fall back to the default database. Fixed by strictly forbidding `DJANGO_ALLOW_DEFAULT_TENANT=true` when `DJANGO_DEBUG=false`, failing closed at startup. | 9/10 |

**Status: Findings 1-8 fixed; regression tests and PostgreSQL integration tests passed.** Security
settings fail closed for missing/placeholder/short production secrets,
wildcard or implicit production hosts, and production `DJANGO_ALLOW_DEFAULT_TENANT=true`. Project endpoints require ready tenant
context; PostgreSQL quota-concurrency and row-locking coverage has been validated live
against PostgreSQL 17. A local regression test also verifies that project creation
requests a subscription lock.
Previously implemented JWT throttles limit token obtain to 5/minute and
refresh to 30/minute.
Debug mode uses an in-process cache; production settings select Django's Redis
cache. The Nginx configuration also limits `/api/auth/`, and Compose binds the
API port to localhost to avoid bypassing the proxy in local development.

The token endpoint is public by design; its existing rate controls are not an
authentication bypass. No unresolved critical or high-severity exploit was
confirmed in the inspected scope.

## Checks performed

- Security review of tenant selection, membership verification, organization switching,
  database alias validation, context cleanup, and project owner permissions.
- Tested membership denial, organization switching, inactive tenant-domain
  rejection, invalid organization rejection, owner-only project visibility,
  invalid credentials, JWT refresh, and rotated refresh-token reuse.
- Verified 10 PostgreSQL tenant-isolation tests against PostgreSQL 17:
  tenant-specific read/write isolation, cross-tenant object ID 404 enforcement,
  unconfigured database alias rejection, unknown/inactive host rejection,
  concurrent thread context isolation, and concurrent project quota enforcement.
- Verified 2 PostgreSQL tenant-provisioning tests against PostgreSQL 17:
  dynamic tenant database creation, database owner enforcement, and partial
  migration failure recovery without dropping existing databases.
- Verified production startup fails closed if `DJANGO_ALLOW_DEFAULT_TENANT=true`
  is set outside debug mode.
- HTTPS redirect, HSTS, and the forwarded-protocol header are configurable for
  production and remain off unless explicitly enabled. `check --deploy` passes
  with complete HTTPS/HSTS settings; enabling them remains conditional on
  verified TLS ingress and HTTPS coverage for every tenant subdomain.
- Confirmed `.env` is ignored by Git. A local `.env` exists in this workspace;
  its contents were not included in this report.
- Read-only review of CI, development Compose credentials, Celery, and Nginx
  configuration.

## Fixes applied

- Fixed JWT refresh returning HTTP 500 by installing
  `rest_framework_simplejwt.token_blacklist` and enabling blacklisting on
  rotation. A regression test verifies rotated refresh-token reuse returns
  401.
- Fixed tenant isolation test suite for PostgreSQL: ensured default subscription
  plans are created idempotently in test fixtures across TransactionTestCase
  table truncations.
- Enforced strict validation on tenant database aliases in `TenantMiddleware`:
  unconfigured static aliases are rejected with `RuntimeError` before fallback,
  while valid dynamic generated aliases are permitted to register.
- Allowed dynamic tenant database provisioning inside `PostgreSQLOrganizationProvisioningTests`
  under the Django test runner while preserving full database operation isolation.
- Separated `projects.delete_project` and `projects.change_project` permissions in
  `IsProjectOwnerOrHasChangePermission` to enforce least privilege on object deletion.
- Enforced dual atomic transactions across `"default"` and tenant databases in
  `ProjectViewSet.perform_create` to ensure database writes complete prior to lock release.
- Added active SQL liveness probe to `/api/health/` with structured 503 error handling.
- Added connection cleanup (`close_old_connections()`) to Celery subscription tasks.
- Enforced that `DJANGO_ALLOW_DEFAULT_TENANT` fails closed at startup if enabled
  outside debug mode.

## Remaining risks and unverified checks

- Redis-backed throttling and Nginx limits were not exercised against live
  services. Production must configure Redis and keep Gunicorn private behind a
  trusted ingress; the included debug cache is per-process and is not suitable
  as a production rate-limit store.
- The shared Python environment's `pip check` reports conflicts involving
  externally installed Firebase Admin/PyJWT and Paddlex/PyYAML packages; no
  dependency vulnerability scanner was available or run.
- Production ingress/TLS, exhaustive role authorization, secret-manager integration,
  container runtime behavior, and penetration testing remain deployment/release work.
