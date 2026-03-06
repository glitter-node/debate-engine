from __future__ import annotations

from google.auth.transport.requests import Request
from google.oauth2 import id_token

VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class GoogleTokenVerificationError(Exception):
    pass


def verify_google_id_token(credential: str, client_id: str) -> dict[str, str]:
    raw_credential = (credential or "").strip()
    raw_client_id = (client_id or "").strip()
    if not raw_credential:
        raise GoogleTokenVerificationError("missing_credential")
    if not raw_client_id:
        raise GoogleTokenVerificationError("missing_client_id")

    try:
        payload = id_token.verify_oauth2_token(raw_credential, Request(), raw_client_id)
    except Exception as exc:
        raise GoogleTokenVerificationError("token_verification_failed") from exc

    issuer = str(payload.get("iss", "")).strip()
    audience = str(payload.get("aud", "")).strip()
    subject = str(payload.get("sub", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    email_verified = payload.get("email_verified")
    is_verified = email_verified is True or str(email_verified).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    if issuer not in VALID_ISSUERS:
        raise GoogleTokenVerificationError("invalid_issuer")
    if audience != raw_client_id:
        raise GoogleTokenVerificationError("invalid_audience")
    if not subject:
        raise GoogleTokenVerificationError("missing_subject")
    if not email:
        raise GoogleTokenVerificationError("missing_email")
    if not is_verified:
        raise GoogleTokenVerificationError("email_not_verified")
    return {"sub": subject, "email": email}
