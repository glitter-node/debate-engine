from __future__ import annotations

from enum import StrEnum


class SiteRole(StrEnum):
    USER = "user"
    MODERATOR = "moderator"
    OPERATOR = "operator"


SITE_ROLE_CHOICES = (
    (SiteRole.USER, "User"),
    (SiteRole.MODERATOR, "Moderator"),
    (SiteRole.OPERATOR, "Operator"),
)

_ROLE_LOOKUP = {
    SiteRole.USER.value: SiteRole.USER,
    SiteRole.MODERATOR.value: SiteRole.MODERATOR,
    SiteRole.OPERATOR.value: SiteRole.OPERATOR,
}


def normalize_role_name(role) -> SiteRole:
    if isinstance(role, SiteRole):
        return role
    value = str(role or "").strip().lower()
    normalized = _ROLE_LOOKUP.get(value)
    if normalized is None:
        raise ValueError(f"Unknown site role: {role!r}")
    return normalized
