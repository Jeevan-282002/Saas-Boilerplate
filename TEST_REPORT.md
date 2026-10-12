# Test Report

**Date:** 2026-10-12  
**Environment:** Windows, Python 3.12.0, Django 5.2.8; project declares Python
>=3.13. Tests used isolated in-memory SQLite for the default database.

## Summary

- Latest pytest run: **11 passed, 0 failed, 9 skipped** (nine non-failing
  `staticfiles` directory warnings). The six skips are the PostgreSQL-only
  integration cases because local settings use SQLite.
- Failed or blocked pytest cases in the final run: **0**.
- One earlier expanded run exposed a JWT refresh failure (9 passed, 1 failed).
  The failure was fixed and the full suite passed afterward.
- Nine PostgreSQL multi-tenant integration tests have now been added and are wired
  into the ephemeral PostgreSQL service in CI, but were **NOT RUN** because
  Docker was unavailable and the locally listening PostgreSQL instance was not
  confirmed as disposable. Compose syntax and CI environment configuration were
  checked; neither a CI runner nor a database was started.
- Redis broker/worker integration and
  container startup/build were **BLOCKED** by unavailable or unverified isolated
  services.

Passing unit tests do not verify production readiness or database-per-tenant
isolation.

## Environment and repository inspection

- Python available to this session: 3.12.0. Python 3.13 is required by
  `pyproject.toml` and is used by CI, but was not installed locally.
- Django: 5.2.8.
- pytest: 8.4.2; pytest-django: 4.14.0; Django REST Framework: 3.15.2;
  SimpleJWT: 5.5.0.
- No Git repository was present, so a Git working-tree/baseline check was not
  possible.
- The `.env` file was absent. `.env.example` was used only temporarily for
  read-only Compose configuration validation and removed afterward.
- Docker CLI is installed, but the Docker daemon was unreachable.
- Redis did not accept connections on localhost:6379.
- A PostgreSQL process was listening on port 5432, but it was not identified as
  an isolated test service and was not accessed. No database or Docker volume
  was created, modified, or removed.
- No Terraform, actionlint, or Nginx validation executable was available.
- Billing, registration, password reset, email verification, audit-event, and
  monitoring implementations were not present in the inspected project.

## Checks executed

| Check | Result | Evidence / notes |
|---|---|---|
| `python -m compileall -q .` | PASSED | Python sources compiled. |
| `python -m ruff check .` | PASSED | No lint findings. |
| `python manage.py check` | PASSED | No Django system-check issues. |
| `python manage.py makemigrations --check --dry-run` | PASSED | No model changes without migrations. |
| `python manage.py check --deploy` | PASSED WITH WARNINGS | Reported missing HSTS and `SECURE_SSL_REDIRECT`; configure HTTPS at Django or trusted ingress before deployment. |
| `python manage.py spectacular --validate ...` | PASSED | OpenAPI generation/validation returned no schema errors. |
| `docker compose config --quiet` | PASSED | Validated with a temporary copy of `.env.example`; did not start services. |
| Celery import/autodiscovery and `example_task.run(...)` | PASSED, LIMITED | Direct in-process invocation only; no broker or worker was exercised. |
| `python -m pip check` | FAILED | Shared interpreter contains unrelated conflicts: Firebase Admin requires PyJWT >=2.10.1 while installed SimpleJWT 5.5.0 requires PyJWT <2.10; Paddlex requires PyYAML 6.0.2 while 6.0.3 is installed. These packages are not declared by this project. |
| Docker build/start, Nginx runtime, Terraform validation, live CI | BLOCKED / NOT RUN | Docker daemon and Terraform/actionlint/Nginx tools were unavailable; no deployment or external CI run was performed. |

## Test coverage actually executed

The final pytest run used SQLite `:memory:` and covered:

- Project create/list and owner-only visibility.
- Membership authorization, organization switching, and rejection of an
  unregistered organization header.
- Rejection of an inactive tenant domain when default-tenant fallback is off.
- JWT obtain, invalid credentials, refresh, and rejection of a refresh token
  after rotation.
- JWT obtain throttling: five invalid attempts from one client identity returned
  401; the sixth returned 429.
- Tenant database-router context selection and central-model routing.
- Django production mode is configured for Redis-backed throttling, but that
  backend was not exercised because Redis was unavailable.
- Nine PostgreSQL integration cases are included and configured for CI. The
  latest SQLite run explicitly skipped all nine. No claim is made that those
  PostgreSQL cases passed in this environment.

The initial JWT refresh test failed with `AttributeError:
OutstandingToken.objects` and an HTTP 500. The SimpleJWT blacklist app was not
installed despite refresh rotation being enabled. The app has now been
installed and refresh-token blacklisting enabled; the regression test and full
suite pass. A security review also found missing JWT endpoint throttling; scoped
DRF throttles, a Redis-backed production cache, Nginx auth-path limits, and
loopback-only local API publishing were added. The rate-limit regression test
passes using the local-memory test cache.

## Not run or blocked

- The added PostgreSQL integration cases were not run locally: separate tenant
  database behavior, cross-tenant read/update/delete by object ID, unconfigured
  tenant alias failure, and concurrent request isolation still require CI or a
  verified disposable PostgreSQL environment.
- Tenant database provisioning, database-user creation, migration execution
  against tenant databases, retry/partial-failure recovery, or concurrent
  provisioning. These workflows are not implemented.
- Complete CRUD edge cases, inactive/expired JWT cases, logout, registration,
  password reset, email verification, and all organization roles.
- Real Redis connectivity, worker execution, retries, task failure handling,
  and task tenant-context propagation.
- Docker image build, Compose service startup, PostgreSQL/Redis health checks
  in containers, Gunicorn/Nginx runtime, and graceful shutdown.
- GitHub Actions workflow execution or schema-aware workflow linting.
- Terraform validation/apply (no Terraform configuration exists; apply was not
  attempted).
- Dependency vulnerability scanning and production penetration testing.

## Reproduction commands

From the project directory in PowerShell, using an isolated Python 3.13
environment with `.[dev]` installed:

```powershell
$env:DATABASE_URL = 'sqlite:///:memory:'
$env:DJANGO_SECRET_KEY = 'qa-local-key-at-least-32-bytes-long-not-production'
$env:DJANGO_DEBUG = 'true'
$env:DJANGO_ALLOW_DEFAULT_TENANT = 'true'
python -m pytest
python -m ruff check .
python -m compileall -q .
python manage.py check
python manage.py makemigrations --check --dry-run
```

The production check was run separately with `DJANGO_DEBUG=false`, a generated
non-production test key, and `python manage.py check --deploy`; it returned
warnings described above. PostgreSQL and Redis integration commands are
intentionally omitted because no isolated test services were configured.
