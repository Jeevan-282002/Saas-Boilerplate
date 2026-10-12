FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system django && adduser --system --ingroup django django

COPY pyproject.toml ./
COPY config ./config
COPY apps ./apps
COPY manage.py ./
RUN pip install --upgrade pip && pip install .
RUN DJANGO_SECRET_KEY=build-only-secret DJANGO_DEBUG=true python manage.py collectstatic --noinput

USER django
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-", "--error-logfile", "-"]
