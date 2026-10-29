import logging
from typing import Any

from apps.tenants.models import AuditLog

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {
    "password",
    "token",
    "secret",
    "access",
    "refresh",
    "token_hash",
    "raw_token",
    "api_key",
    "authorization",
}


def log_audit_event(
    tenant,
    action: str,
    resource_type: str,
    resource_id: Any = "",
    actor=None,
    metadata: dict | None = None,
    request=None,
    using: str = "default",
) -> AuditLog | None:
    """Safely records an organization audit event."""
    if tenant is None:
        return None

    ip_address = None
    if request is not None:
        if actor is None and getattr(request, "user", None) and request.user.is_authenticated:
            actor = request.user
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            ip_address = forwarded.split(",")[0].strip()
        else:
            ip_address = request.META.get("REMOTE_ADDR")
        if ip_address and len(ip_address) > 45:
            ip_address = ip_address[:45]

    safe_metadata: dict[str, Any] = {}
    if metadata:
        for k, v in metadata.items():
            if str(k).lower() in SENSITIVE_KEYS:
                continue
            safe_metadata[str(k)] = (
                str(v) if not isinstance(v, (int, float, bool, list, dict)) else v
            )

    try:
        return AuditLog.objects.using(using).create(
            tenant=tenant,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else "",
            metadata=safe_metadata,
            ip_address=ip_address,
        )
    except Exception as exc:
        logger.error(
            "Failed to write audit log for tenant %s action %s: %s",
            getattr(tenant, "slug", tenant),
            action,
            exc,
        )
        return None
