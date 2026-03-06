from __future__ import annotations

from functools import wraps

from django.contrib.auth.mixins import AccessMixin
from django.http import HttpResponse

from .models import UserRole
from .site_roles import SiteRole, normalize_role_name


def ensure_user_role(user):
    if not user or not user.is_authenticated:
        return None
    role_obj, _ = UserRole.objects.get_or_create(
        user=user, defaults={"role": SiteRole.USER}
    )
    return role_obj


def user_has_site_role(user, *roles) -> bool:
    role_obj = ensure_user_role(user)
    if role_obj is None:
        return False
    return role_obj.role in normalize_roles(*roles)


def normalize_roles(*roles) -> set[str]:
    normalized = set()
    for role in roles:
        if role is None:
            continue
        normalized.add(normalize_role_name(role))
    return normalized


def _forbidden_response() -> HttpResponse:
    return HttpResponse(
        "Forbidden", status=403, content_type="text/plain; charset=utf-8"
    )


def enforce_site_roles(request, *roles):
    if not request.user.is_authenticated:
        return None
    required_roles = normalize_roles(*roles)
    if user_has_site_role(request.user, *required_roles):
        return None
    return _forbidden_response()


class RoleRequiredMixin(AccessMixin):
    required_roles: tuple[SiteRole | str, ...] = ()

    def get_required_roles(self) -> tuple[SiteRole | str, ...]:
        return self.required_roles

    def handle_role_forbidden(self):
        return _forbidden_response()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        required_roles = self.get_required_roles()
        if user_has_site_role(request.user, *required_roles):
            return super().dispatch(request, *args, **kwargs)
        return self.handle_role_forbidden()


def role_required(*roles):
    normalized_roles = tuple(normalize_roles(*roles))

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                mixin = AccessMixin()
                mixin.request = request
                return mixin.handle_no_permission()
            rejection = enforce_site_roles(request, *normalized_roles)
            if rejection is not None:
                return rejection
            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator
