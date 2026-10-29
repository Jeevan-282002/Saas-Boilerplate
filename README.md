# Django Boilerplate

A Python 3.13 / Django 5.2 LTS starter for tenant-aware REST APIs. It includes
PostgreSQL, Django REST Framework, JWT authentication, Django's groups and
permissions, Celery with Redis, OpenAPI docs, pytest, Gunicorn, Docker Compose,
Nginx, structured console logs, and GitHub Actions CI.

## Quick start

1. Copy `.env.example` to `.env` and set a strong `DJANGO_SECRET_KEY`.
2. Start services with `docker compose up --build`.
3. Open `http://localhost:8000/api/docs/`.

For local Python development, the project defaults to SQLite unless you explicitly
opt in to `.env` loading with `DJANGO_LOAD_DOTENV=true`. This keeps local
pytest runs and host-based development from unexpectedly connecting to the Docker
Postgres service. Docker Compose injects its own environment values, so the
containerized app still uses the Postgres settings defined in `compose.yaml`.

Django defaults to `DEBUG=false` and requires an explicit `DJANGO_SECRET_KEY`;
production keys must contain at least 50 characters, and example/development
placeholder values are rejected. For local Python development, set
`DJANGO_DEBUG=true` and provide a non-placeholder key in the shell because
`.env` is not loaded by default. For production, keep debug disabled, use a
unique secret from a secret manager, configure `DJANGO_ALLOWED_HOSTS` and CSRF
trusted origins, and set `DJANGO_ALLOW_DEFAULT_TENANT=false`. The
`.env.example` values are for local development only.

Create a Python 3.13 virtual environment, install `pip install -e ".[dev]"`,
then run `python manage.py migrate` and `python manage.py runserver`.

## API

- `POST /api/auth/token/` — obtain access and refresh tokens.
- `POST /api/auth/token/refresh/` — rotate a refresh token.
- `GET /api/organizations/` — list the authenticated user's active organizations.
- `POST /api/organizations/` — create an organization and provision its tenant database.
- `GET /api/plans/` — list active Free, Pro, and Business plans.
- `GET /api/subscription/` — view the selected organization's subscription.
- `GET /api/subscription/usage/` — view its user and project usage.
- `POST /api/subscription/cancel/` — organization admins schedule or immediately cancel a local subscription.
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
organization by sending a JWT-authenticated request:

```http
POST /api/organizations/
Authorization: Bearer <access-token>
Content-Type: application/json

{"name":"Acme"}
```

The API derives a unique slug and a domain using `TENANT_DOMAIN_SUFFIX`, creates
the central tenant and an `admin` membership for the creator, creates a
dedicated PostgreSQL database, and applies migrations before activating the
organization. `GET /api/organizations/` continues to return only the user's
active organizations. Creation is limited to five requests per hour. If
provisioning fails, the organization stays inactive and can be retried by its
admin using the same name; successful duplicate submissions return a conflict.
Transient PostgreSQL connection failures get up to three total provisioning
attempts (the initial attempt plus two retries). If a database was created
before a later step failed, it is retained and reused on retry; tenant
migrations are rerun idempotently, and the tenant remains inactive until
provisioning and central subscription setup both succeed.

Subscriptions and plans are stored centrally. Newly provisioned organizations
start on the active Free plan. Select an organization with `X-Organization`
for subscription and usage APIs; membership is checked from the central
database. Only organization admins can cancel subscriptions. Cancellation is
scheduled for the end of the current billing period by default; immediate
cancellation is available with `{"immediately":true}`. Celery Beat runs the
hourly expiration task; database operational failures are retried up to three
times with backoff and jitter. Configure Celery Beat in production as well. Renewal
dates/status are updated only through an authenticated future payment-provider
integration. The provider protocol includes verified webhook events, but no
payment gateway or simulated payment flow is included.

Plan prices, billing intervals, user limits, and JSON usage limits are
editable in Django admin. The initial migration seeds USD monthly defaults:
Free ($0, 5 users, 3 projects), Pro ($29, 25 users, 100 projects), and Business
($99, unlimited users/projects). These are starter values, not pricing advice.
User limits are enforced when creating memberships; the project limit is
enforced when creating projects. Requests that exceed limits or have inactive,
past-due, canceled, or expired subscriptions are denied.

For local Compose development, the example environment uses the `localhost`
domain suffix. Tenant APIs can be selected with the `X-Organization` header,
so wildcard DNS is not required for local smoke tests.

Send the selected organization's slug on tenant-scoped API requests using the
`X-Organization` header, for example `X-Organization: company-b`. The same
JWT account can then switch organizations by changing that header; each request
is checked against the user's membership before tenant database routing. When
the header is omitted, a tenant's configured domain selects its organization.
The header does not grant membership or bypass domain/tenant database setup.

## Tenant database setup

The central/default database stores the tenant registry and Django auth data.
Each tenant's domain maps to a database alias through `apps.tenants`; project
records are routed to that tenant database. The development `DJANGO_ALLOW_DEFAULT_TENANT=true` option sends unregistered
hosts to the default database; production rejects this setting at startup when
`DJANGO_DEBUG=false`. Project API requests also require an active, fully
provisioned organization context; an unregistered host cannot access projects
through the default database fallback.

New tenant databases are registered dynamically by each API process from the
central database connection settings. Existing manually configured tenant
aliases in `DJANGO_TENANT_DATABASES` remain supported. Set
`TENANT_DATABASE_ADMIN_URL` to a PostgreSQL connection URL whose role is
permitted to create databases owned by the runtime user configured in
`DATABASE_URL` (for example, a dedicated `CREATEDB` role with membership in
the runtime role). The API never accepts database URLs or credentials from
requests. Keep the provisioning URL in a secret manager in production and
restrict its role to the privileges needed to create tenant databases. The
Compose example uses its local `app` role, which is privileged by the
PostgreSQL development image and is not a production configuration.

Set `TENANT_DOMAIN_SUFFIX` to the DNS suffix used by your deployment, and
configure wildcard DNS and ingress routing before using tenant domains in
production. Generated tenant database names and aliases use random identifiers;
SQL identifiers are quoted by the PostgreSQL driver.

To continue using preconfigured tenant databases, configure them with JSON in
`DJANGO_TENANT_DATABASES`, for example:

```json
{"acme":"postgresql://app:secret@db/acme"}
```

For preconfigured aliases, create a `Tenant` row in the default database with
the corresponding domain and database alias. Apply schema migrations to the
default database and to each configured tenant database. Django migrations are
routed so tenant app tables are created only on non-default aliases; centrally
managed Django and tenant registry tables are created only on `default`. Run
migrations with an explicit alias for each tenant, for example:

```shell
python manage.py migrate --database default
python manage.py migrate --database acme
```

Keep tenant and central models separated: Django relations cannot cross
databases. Database deletion, tenant offboarding, secret rotation, backups, and
migration orchestration after a tenant is created remain deployment
responsibilities.

## Deployment notes

The included Compose setup is for development. Its optional Nginx profile
listens on plain HTTP port 80; it does not terminate TLS. Put a TLS-configured
Nginx or managed ingress in front of Gunicorn for production. Configure
`DJANGO_SECURE_SSL_REDIRECT=true` and a positive
`DJANGO_SECURE_HSTS_SECONDS` only after HTTPS is served for every hostname.
HSTS subdomains and preload remain off unless all tenant subdomains meet those
requirements.

If TLS terminates before Django, set `DJANGO_TRUST_X_FORWARDED_PROTO=true` only
when the trusted ingress overwrites `X-Forwarded-Proto` and Gunicorn cannot be
reached directly by clients. Otherwise, clients can spoof that header and
bypass Django's HTTPS redirect. The default does not trust forwarded-protocol
headers. In production also configure `DJANGO_ALLOWED_HOSTS`,
`DJANGO_CSRF_TRUSTED_ORIGINS`, secure cookies, secret management, backups, and
monitoring. Run `python manage.py check --deploy` with the actual deployment
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
concurrent quota enforcement and tenant context isolation. PostgreSQL tenant
provisioning recovery cases also run in CI. A CI assertion fails if any of the
12 PostgreSQL integration cases is missing, skipped, duplicated, or fails.
They are skipped locally unless the
default, `tenant_a`, and `tenant_b`
database aliases are all configured with PostgreSQL URLs. Do not point this
test suite at production or a database containing user data; Django creates and
drops dedicated test databases for the configured aliases.

The test role must be allowed to create/drop Django test databases and create
tenant databases for the provisioning recovery cases. Set `DATABASE_URL`,
`DJANGO_TENANT_DATABASES`, and `TENANT_DATABASE_ADMIN_URL` to disposable local
PostgreSQL databases; never run these tests against production or shared
databases.

Production HTTPS settings are intentionally opt-in because the bundled Nginx
profile only serves HTTP and does not terminate TLS. Configure
`DJANGO_SECURE_SSL_REDIRECT=true` and positive HSTS only when a TLS-enabled
ingress serves all relevant tenant domains. Enable
`DJANGO_TRUST_X_FORWARDED_PROTO=true` only when that trusted ingress overwrites
the header and direct Gunicorn access is blocked.
