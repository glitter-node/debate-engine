from __future__ import annotations

import json
import logging

from api.middleware.ip_resolver import get_client_ip
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.http import HttpRequest, JsonResponse
from django.urls import reverse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .env import get_authflow_settings
from .forms import AccessRequestForm
from .google_oauth import GoogleTokenVerificationError, verify_google_id_token
from .mail import send_access_email
from .models import GoogleAccountLink
from .rate_limit import allow_access_request, allow_google_onetap_request
from .tokens import issue_email_key, normalize_email, verify_email_key
from thinking.audit import log_action
from thinking.roles import ensure_user_role

logger = logging.getLogger(__name__)

GENERIC_NOTICE = "If the address is eligible, a sign-in link will be emailed shortly."


def _verify_url(lookup_key: str) -> str:
    cfg = get_authflow_settings()
    return f"{cfg.base_url}/auth/verify?k={lookup_key}"


def _resolve_user(email: str):
    user_model = get_user_model()
    existing = user_model.objects.filter(email__iexact=email).first()
    if existing:
        ensure_user_role(existing)
        return existing

    base = email.split("@", 1)[0] or "user"
    slug = "".join(ch for ch in base.lower() if ch.isalnum() or ch in {"_", "-", "."})
    slug = slug[:120] or "user"
    username = slug
    idx = 2
    while user_model.objects.filter(username=username).exists():
        suffix = f"-{idx}"
        username = f"{slug[:150-len(suffix)]}{suffix}"
        idx += 1

    user = user_model.objects.create_user(username=username, email=email)
    user.set_unusable_password()
    user.save(update_fields=["password"])
    ensure_user_role(user)
    return user


def _safe_next_url(request: HttpRequest, candidate: str | None) -> str:
    default_next = reverse("thinking:thesis_create")
    raw = (candidate or "").strip()
    if not raw:
        return default_next
    if url_has_allowed_host_and_scheme(
        raw,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return raw
    return default_next


def _build_google_username(google_sub: str) -> str:
    user_model = get_user_model()
    base = f"google_{google_sub}"[:150]
    username = base
    idx = 2
    while user_model.objects.filter(username=username).exists():
        suffix = f"_{idx}"
        username = f"{base[:150-len(suffix)]}{suffix}"
        idx += 1
    return username


def _resolve_google_user(google_sub: str, email: str):
    user_model = get_user_model()
    link = (
        GoogleAccountLink.objects.select_related("user")
        .filter(google_sub=google_sub)
        .first()
    )
    if link:
        user = link.user
    else:
        user = user_model.objects.filter(email__iexact=email).first()
        if user is None:
            user = user_model.objects.filter(username=f"google_{google_sub}").first()
        if user is None:
            user = user_model.objects.create_user(
                username=_build_google_username(google_sub),
                email=email,
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
    if (user.email or "").strip().lower() != email:
        user.email = email
        user.save(update_fields=["email"])
    GoogleAccountLink.objects.update_or_create(
        google_sub=google_sub,
        defaults={"user": user, "email": email},
    )
    ensure_user_role(user)
    return user


@require_GET
def auth_home(request: HttpRequest):
    form = AccessRequestForm()
    next_path = _safe_next_url(request, request.GET.get("next"))
    ctx = {
        "form": form,
        "notice": GENERIC_NOTICE if request.GET.get("sent") else "",
        "invalid": bool(request.GET.get("invalid")),
        "google_client_id": settings.GOOGLE_CLIENT_ID,
        "next_path": next_path,
    }
    return render(request, "authflow/request_access.html", ctx)


@require_POST
def request_access(request: HttpRequest):
    form = AccessRequestForm(request.POST)
    if not form.is_valid():
        return redirect("/auth/?sent=1")

    email = normalize_email(form.cleaned_data["email"])
    client_ip = get_client_ip(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "")

    if not allow_access_request(email=email, client_ip=client_ip):
        logger.warning("authflow_rate_limited email=%s ip=%s", email, client_ip)
        return redirect("/auth/?sent=1")

    lookup_key = issue_email_key(
        email=email, request_ip=client_ip, user_agent=user_agent
    )
    if get_authflow_settings().mail_enabled:
        send_access_email(email=email, verify_url=_verify_url(lookup_key))
    else:
        logger.info("authflow_mail_disabled key_issued=true email=%s", email)

    return redirect("/auth/?sent=1")


@require_GET
def verify_access(request: HttpRequest):
    cfg = get_authflow_settings()
    raw_key = request.GET.get("k", "")
    result = verify_email_key(raw_key)
    if not result.ok:
        logger.info("authflow_verify_failed reason=%s", result.reason)
        return redirect("/auth/?invalid=1")

    user = _resolve_user(result.email)
    login(request, user)
    log_action(
        actor=user,
        action="auth.login_via_email",
        target=user,
        metadata={"source": "authflow.verify"},
        request=request,
    )
    return redirect(cfg.post_verify_redirect_path)


@require_POST
def google_onetap(request: HttpRequest):
    try:
        payload = json.loads((request.body or b"").decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)
    credential = str(payload.get("credential", "")).strip()
    next_url = _safe_next_url(request, payload.get("next"))
    client_id = (settings.GOOGLE_CLIENT_ID or "").strip()
    if not client_id:
        return JsonResponse({"ok": False, "error": "google_login_disabled"}, status=503)
    try:
        verified = verify_google_id_token(credential=credential, client_id=client_id)
    except GoogleTokenVerificationError as exc:
        logger.info("authflow_google_verify_failed reason=%s", exc)
        return JsonResponse({"ok": False, "error": "invalid_google_token"}, status=401)
    google_sub = verified["sub"]
    email = verified["email"]
    client_ip = get_client_ip(request)
    if not allow_google_onetap_request(
        google_sub=google_sub,
        email=email,
        client_ip=client_ip,
    ):
        logger.warning(
            "authflow_google_rate_limited sub=%s email=%s ip=%s",
            google_sub,
            email,
            client_ip,
        )
        return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)
    user = _resolve_google_user(google_sub=google_sub, email=email)
    backend = (
        settings.AUTHENTICATION_BACKENDS[0]
        if getattr(settings, "AUTHENTICATION_BACKENDS", None)
        else "django.contrib.auth.backends.ModelBackend"
    )
    login(request, user, backend=backend)
    log_action(
        actor=user,
        action="auth.login_via_google",
        target=user,
        metadata={"source": "authflow.google_onetap"},
        request=request,
    )
    return JsonResponse({"ok": True, "next": next_url})
