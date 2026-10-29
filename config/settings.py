import json
import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
if os.environ.get("DJANGO_LOAD_DOTENV", "false").lower() == "true":
    load_dotenv(BASE_DIR / ".env")

DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("DJANGO_SECRET_KEY must be set")
if SECRET_KEY in {
    "replace-with-a-long-random-secret",
    "unsafe-development-key-change-me",
}:
    raise ValueError("Replace the example or development DJANGO_SECRET_KEY")
if not DEBUG and len(SECRET_KEY) < 50:
    raise ValueError("DJANGO_SECRET_KEY must contain at least 50 characters outside debug mode")
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
if not DEBUG and (
    not os.environ.get("DJANGO_ALLOWED_HOSTS")
    or any(host == "*" or host.startswith(".") for host in ALLOWED_HOSTS)
):
    raise ValueError("Set explicit, non-wildcard DJANGO_ALLOWED_HOSTS outside debug mode")
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "apps.tenants",
    "apps.projects",
    "apps.subscriptions",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.tenants.middleware.TenantMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get(
            "DATABASE_URL",
            "sqlite:///" + str(BASE_DIR / "db.sqlite3").replace("\\", "/"),
        ),
        conn_max_age=60,
        conn_health_checks=True,
    )
}
try:
    tenant_databases = json.loads(os.environ.get("DJANGO_TENANT_DATABASES", "{}"))
except json.JSONDecodeError as exc:
    raise ValueError("DJANGO_TENANT_DATABASES must be valid JSON") from exc
if not isinstance(tenant_databases, dict):
    raise ValueError("DJANGO_TENANT_DATABASES must be a JSON object of alias-to-URL entries")
for alias, database_url in tenant_databases.items():
    if alias == "default" or not isinstance(database_url, str):
        raise ValueError("Tenant database aliases must be non-default names with URL values")
    DATABASES[alias] = dj_database_url.parse(
        database_url,
        conn_max_age=60,
        conn_health_checks=True,
    )

DATABASE_ROUTERS = ["apps.tenants.db_router.TenantDatabaseRouter"]
TENANT_DATABASE_APPS = {"projects"}
DEFAULT_TENANT_ALIAS = os.environ.get("DJANGO_DEFAULT_TENANT_ALIAS", "default")
ALLOW_DEFAULT_TENANT = os.environ.get("DJANGO_ALLOW_DEFAULT_TENANT", "false").lower() == "true"
if not DEBUG and ALLOW_DEFAULT_TENANT:
    raise ValueError("DJANGO_ALLOW_DEFAULT_TENANT must be disabled outside debug mode")
TENANT_DATABASE_ADMIN_URL = os.environ.get("TENANT_DATABASE_ADMIN_URL")
TENANT_DOMAIN_SUFFIX = os.environ.get("TENANT_DOMAIN_SUFFIX", "localhost")

if DEBUG:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "django-boilerplate-development",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": os.environ.get(
                "DJANGO_CACHE_URL",
                os.environ.get("REDIS_URL", "redis://localhost:6379/1"),
            ),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.tenants.authentication.TenantJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_RATES": {
        "token_obtain": "5/min",
        "token_refresh": "30/min",
        "organization_create": "5/hour",
        "subscription_read": "60/min",
    },
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Django Boilerplate API",
    "DESCRIPTION": "Tenant-aware REST API starter.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_BEAT_SCHEDULE = {
    "expire-subscriptions-hourly": {
        "task": "apps.subscriptions.tasks.expire_subscriptions",
        "schedule": 60 * 60,
    },
}

SECURE_SSL_REDIRECT = (
    not DEBUG and os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "false").lower() == "true"
)
SECURE_HSTS_SECONDS = 0 if DEBUG else int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = (
    not DEBUG and os.environ.get("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", "false").lower() == "true"
)
SECURE_HSTS_PRELOAD = (
    not DEBUG and os.environ.get("DJANGO_SECURE_HSTS_PRELOAD", "false").lower() == "true"
)
if not DEBUG and os.environ.get("DJANGO_TRUST_X_FORWARDED_PROTO", "false").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": (
                '{"time":"%(asctime)s","level":"%(levelname)s",'
                '"logger":"%(name)s","message":"%(message)s"}'
            ),
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
    },
}
