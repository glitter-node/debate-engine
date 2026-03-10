from django.db import IntegrityError

from api.middleware.ip_resolver import get_client_ip

from ..auto_moderation import maybe_auto_moderate_after_report
from ..models import ContentReport
from ..roles import ensure_user_role


def submit_content_report(*, request, target, target_type: str, allowed_reasons: tuple[str, ...]):
    reason = request.POST.get("reason", "").strip().lower()
    if reason not in allowed_reasons:
        reason = "other"
    detail = request.POST.get("detail", "").strip()

    role_obj = ensure_user_role(request.user)
    reporter_role = role_obj.role if role_obj else ""

    target_fk_name = "thesis" if target_type == ContentReport.TargetType.THESIS else "counter"
    target_lookup = {target_fk_name: target}
    report_kwargs = {
        "reporter": request.user,
        "reporter_role": reporter_role,
        "reason": reason,
        "detail": detail,
        "ip_address": get_client_ip(request),
        "user_agent": request.META.get("HTTP_USER_AGENT", ""),
        **target_lookup,
    }

    created = False
    try:
        _, created = ContentReport.objects.get_or_create(
            reporter=request.user,
            status=ContentReport.Status.OPEN,
            defaults=report_kwargs,
            **target_lookup,
        )
    except IntegrityError:
        created = False

    maybe_auto_moderate_after_report(target=target, request=request)
    return {"created": created, "reason": reason}
