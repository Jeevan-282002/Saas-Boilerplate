# Security Review

**Date:** 2026-10-12  
**Scope:** Read-only review of the Django tenant middleware, JWT
authentication, database router, project permissions and serializer, settings,
Celery task, Docker/Compose, Nginx, and CI workflow. Runtime authorization checks
were limited to the SQLite pytest suite.

## Findings

| # | Severity | File | Lines | Vulnerability | Confidence |
|---|----------|------|-------|---------------|------------|
| 1 | 🟡 MEDIUM | [config/settings.py](./config/settings.py), [config/urls.py](./config/urls.py), [apps/tenants/views.py](./apps/tenants/views.py) | 142-160; 25-26; 13-21 | JWT token acquisition was initially configured without request throttling, permitting repeated online password guesses against `/api/auth/token/`. Fixed with scoped DRF throttles and ingress rate limits. | 9/10 |

**Status: FIXED and regression-tested.** Scoped throttles now limit token obtain
to 5/minute and refresh to 30/minute. A test verifies that the sixth token
obtain attempt from the same client identity is rejected with HTTP 429. Debug
mode uses an in-process cache; production settings select Django's Redis cache.
The Nginx configuration also limits `/api/auth/`, and Compose binds the API
port to localhost to avoid bypassing the proxy in local development.

The token endpoint is public by design; this finding is about the absence of
rate controls, not an authentication bypass. The repository does not establish
whether a production ingress or external identity layer adds compensating
controls. No critical or high-severity exploit was confirmed in the inspected
scope.

## Checks performed

- Read-only security review of tenant selection, membership verification,
  organization switching, database alias validation, context cleanup, and
  project owner permissions.
- Tested membership denial, organization switching, inactive tenant-domain
  rejection, invalid organization rejection, owner-only project visibility,
  invalid credentials, JWT refresh, and rotated refresh-token reuse.
- Reviewed production-mode Django security-check output; it warns that HSTS and
  Django HTTPS redirect are not enabled.
- Confirmed settings require an explicit secret key when `DEBUG` is false and
  that `.env` is ignored by Git. No local `.env` file was present.
- Read-only review of CI, development Compose credentials, Celery, and Nginx
  configuration. No services were started.

## Fixes applied

- Fixed JWT refresh returning HTTP 500 by installing
  `rest_framework_simplejwt.token_blacklist` and enabling blacklisting on
  rotation. A regression test verifies rotated refresh-token reuse returns
  401.

## Remaining risks and unverified checks

- Redis-backed throttling and Nginx limits were not exercised against live
  services. Production must configure Redis and keep Gunicorn private behind a
  trusted ingress; the included debug cache is per-process and is not suitable
  as a production rate-limit store.
- Database-per-tenant isolation was not tested against isolated PostgreSQL
  databases. In particular, cross-tenant IDOR, failed connections, transaction
  boundaries, and concurrent request isolation remain unverified.
- The shared Python environment's `pip check` reports conflicts involving
  externally installed Firebase Admin/PyJWT and Paddlex/PyYAML packages; no
  dependency vulnerability scanner was available or run.
- Production HSTS/HTTPS configuration, authentication throttling, exhaustive
  role authorization, secret-manager integration, container runtime behavior,
  and penetration testing remain deployment/release work.
- No exploitable tenant-isolation or object-permission issue was established
  by the limited tests; their scope is insufficient to certify isolation.
