from __future__ import annotations

from typing import Any

from api.middleware.ip_resolver import get_client_ip

from .models import AuditLog
from .roles import ensure_user_role


def _target_info(target) -> tuple[str, str | None]:
    if target is None:
        return "", None
    model = getattr(target, "_meta", None)
    target_model = model.label_lower if model else target.__class__.__name__.lower()
    target_id = getattr(target, "pk", None)
    return target_model, (str(target_id) if target_id is not None else None)


def log_action(
    *,
    actor,
    action: str,
    target=None,
    metadata: dict[str, Any] | None = None,
    request=None,
):
    actor_obj = actor if getattr(actor, "is_authenticated", False) else None
    actor_role = ""
    if actor_obj is not None:
        role_obj = ensure_user_role(actor_obj)
        actor_role = role_obj.role if role_obj else ""

    ip_address = None
    user_agent = ""
    if request is not None:
        ip_address = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

    target_model, target_id = _target_info(target)
    return AuditLog.objects.create(
        actor=actor_obj,
        actor_role=actor_role,
        action=action,
        target_model=target_model,
        target_id=target_id,
        metadata=dict(metadata or {}),
        ip_address=ip_address,
        user_agent=user_agent,
    )
