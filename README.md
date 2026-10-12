# Django Boilerplate

A Python 3.13 / Django 5.2 LTS starter for tenant-aware REST APIs. It includes
PostgreSQL, Django REST Framework, JWT authentication, Django's groups and
permissions, Celery with Redis, OpenAPI docs, pytest, Gunicorn, Docker Compose,
Nginx, structured console logs, and GitHub Actions CI.

## Quick start

1. Copy `.env.example` to `.env` and set a strong `DJANGO_SECRET_KEY`.
2. Start services with `docker compose up --build`.
3. Open `http://localhost:8000/api/docs/`.

For local Python development, create a Python 3.13 virtual environment, install
`pip install -e ".[dev]"`, then run `python manage.py migrate` and
`python manage.py runserver`.

## API

- `POST /api/auth/token/` — obtain access and refresh tokens.
- `POST /api/auth/token/refresh/` — rotate a refresh token.
- `GET /api/organizations/` — list the authenticated user's organizations.
- `/api/projects/` — example authenticated CRUD API.
- `/api/health/` — checks the default database connection.
- `/api/schema/`, `/api/docs/`, `/api/redoc/` — OpenAPI schema and UI.

JWT token obtain is limited to 5 requests per minute per client identity, and
refresh to 30 per minute. Production uses Redis-backed Django throttling; debug
mode uses a process-local cache for development only. The included Nginx proxy
also limits `/api/auth/` requests. Keep Gunicorn private behind the trusted
ingress so clients cannot bypass edge limits or spoof forwarded client IPs.

Create users and assign Django groups/permissions through `/admin/`. Projects
store the central user's numeric ID (rather than a cross-database foreign key).
Project owners can access their own projects by default; users granted the
`projects.view_project` or `projects.change_project` permission can access
broader project sets.

## Organization memberships and switching

A user account can have memberships in multiple organizations. Create an
organization in `/admin/`, then add a membership for the existing user; the
membership has a unique user/organization pair and an `admin` or `member` role.
`GET /api/organizations/` returns only the authenticated user's active
organizations.

Send the selected organization's slug on tenant-scoped API requests using the
`X-Organization` header, for example `X-Organization: company-b`. The same
JWT account can then switch organizations by changing that header; each request
is checked against the user's membership before tenant database routing. When
the header is omitted, a tenant's configured domain selects its organization.
The header does not grant membership or bypass domain/tenant database setup.

## Tenant database setup

The central/default database stores the tenant registry and Django auth data.
Each tenant's domain maps to a database alias through `apps.tenants`; project
records are routed to that tenant database. The development
`DJANGO_ALLOW_DEFAULT_TENANT=true` option sends unregistered hosts to the
default database and must be disabled in production.

Configure tenant databases with JSON in `DJANGO_TENANT_DATABASES`, for example:

```json
{"acme":"postgresql://app:secret@db/acme"}
```

Create a `Tenant` row in the default database with `domain=acme.example.com`
and `database_alias=acme`. Apply schema migrations to the default database and
to each configured tenant database. Django migrations are routed so tenant app
tables are created only on non-default aliases; centrally managed Django and
tenant registry tables are created only on `default`. Run migrations with an
explicit alias for each tenant, for example:

```shell
python manage.py migrate --database default
python manage.py migrate --database acme
```

Tenant credentials should come from a cloud secret manager or another secure
runtime configuration source. Do not commit them in environment files. Keep
tenant and central models separated: Django relations cannot cross databases.
Database-per-tenant provisioning, secret rotation, tenant lifecycle operations,
and per-tenant migration orchestration are deployment responsibilities and
should be implemented before production use.

## Deployment notes

The included Compose setup is for development. Put Nginx or a managed ingress
in front of Gunicorn for production TLS and request routing. Use PgBouncer when
connection volume warrants it. Configure Django `ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, secure cookies, HTTPS redirects, trusted proxy headers,
secret management, backups, and monitoring for the chosen deployment
environment. Terraform is left cloud-neutral until a provider and hosting
target are selected.

To try the included Nginx reverse-proxy configuration locally, start the
`proxy` Compose profile and open `http://localhost/`.

## Checks

```shell
ruff check .
pytest
```

The GitHub Actions test job uses its disposable PostgreSQL service to create
isolated `tenant_a` and `tenant_b` test databases and runs the PostgreSQL
isolation cases in `apps/projects/test_postgres_tenant_isolation.py`. The cases
cover tenant-specific reads and writes, cross-tenant object IDs, unknown and
inactive tenants, membership denial, unavailable database behavior, and
concurrent tenant context isolation. They are skipped locally unless the
default, `tenant_a`, and `tenant_b`
database aliases are all configured with PostgreSQL URLs. Do not point this
test suite at production or a database containing user data; Django creates and
drops dedicated test databases for the configured aliases.
