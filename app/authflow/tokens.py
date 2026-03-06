from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import EmailAuthToken

TOKEN_TTL_SECONDS = 30 * 60


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def hash_lookup_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _generate_lookup_key() -> str:
    # 18 random bytes -> 24 base64url chars. Short, random, URL-safe.
    return base64.urlsafe_b64encode(secrets.token_bytes(18)).decode("ascii").rstrip("=")


def issue_email_key(
    email: str,
    request_ip: str = "",
    user_agent: str = "",
) -> str:
    normalized_email = normalize_email(email)
    raw_key = _generate_lookup_key()

    EmailAuthToken.objects.create(
        email=normalized_email,
        key_hash=hash_lookup_key(raw_key),
        expires_at=timezone.now() + timedelta(seconds=TOKEN_TTL_SECONDS),
        request_ip=(request_ip or "")[:45] or None,
        user_agent=(user_agent or "")[:1000],
    )
    return raw_key


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    email: str = ""
    reason: str = ""
    token_obj_id: int = 0


def verify_email_key(raw_key: str) -> VerifyResult:
    raw = (raw_key or "").strip()
    if not raw:
        return VerifyResult(ok=False, reason="missing-key")

    key_digest = hash_lookup_key(raw)
    now = timezone.now()
    with transaction.atomic():
        token_obj = (
            EmailAuthToken.objects.select_for_update()
            .filter(key_hash=key_digest)
            .first()
        )
        if token_obj is None:
            return VerifyResult(ok=False, reason="not-found")
        if token_obj.used_at is not None:
            return VerifyResult(ok=False, reason="already-used")
        if token_obj.expires_at <= now:
            return VerifyResult(ok=False, reason="expired")
        token_obj.used_at = now
        token_obj.save(update_fields=["used_at"])

    return VerifyResult(
        ok=True,
        email=normalize_email(token_obj.email),
        token_obj_id=token_obj.id,
    )
