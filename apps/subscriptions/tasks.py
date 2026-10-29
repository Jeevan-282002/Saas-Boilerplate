from celery import shared_task
from django.db import OperationalError, close_old_connections

from apps.subscriptions.services import expire_due_subscriptions


@shared_task(
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def expire_subscriptions():
    close_old_connections()
    try:
        return expire_due_subscriptions()
    finally:
        close_old_connections()
