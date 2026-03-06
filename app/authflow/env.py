from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

from DjangoProto8.config import parse_bool
from django.conf import settings


@dataclass(frozen=True)
class AuthFlowSettings:
    base_url: str
    post_verify_redirect_path: str
    mail_enabled: bool
    mail_server: str
    mail_port: int
    mail_from: str
    mail_from_name: str
    mail_use_tls: bool
    mail_use_ssl: bool
    mail_require_tls: bool
    mail_allow_invalid_cert: bool
    smtp_username: str
    smtp_password: str


def _normalize_base_url(raw: str) -> str:
    parsed = urlparse((raw or "").strip())
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError("BASE_URL must be an absolute URL.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise RuntimeError("BASE_URL must not include path/query/fragment.")
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("BASE_URL scheme must be http or https.")
    if parsed.scheme != "https" and not settings.DEBUG:
        raise RuntimeError("BASE_URL must use https unless DEBUG is true.")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _require(name: str, value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise RuntimeError(f"{name} is required when MAIL_ENABLED is true.")
    return cleaned


def _required_bool_env(name: str) -> bool:
    raw = _require(name, os.environ.get(name, ""))
    return parse_bool(raw, default=False)


@lru_cache(maxsize=1)
def get_authflow_settings() -> AuthFlowSettings:
    mail_enabled = parse_bool(os.environ.get("MAIL_ENABLED"), default=False)
    if mail_enabled:
        base_url = _normalize_base_url(
            _require("BASE_URL", os.environ.get("BASE_URL", ""))
        )
    else:
        fallback = "http://localhost:8000" if settings.DEBUG else "https://localhost"
        base_url = _normalize_base_url(os.environ.get("BASE_URL", fallback))

    post_verify_redirect_path = (
        os.environ.get("POST_VERIFY_REDIRECT_PATH", "/").strip() or "/"
    )
    if not post_verify_redirect_path.startswith("/"):
        raise RuntimeError("POST_VERIFY_REDIRECT_PATH must start with '/'.")

    if not mail_enabled:
        return AuthFlowSettings(
            base_url=base_url,
            post_verify_redirect_path=post_verify_redirect_path,
            mail_enabled=False,
            mail_server="",
            mail_port=0,
            mail_from="",
            mail_from_name="",
            mail_use_tls=False,
            mail_use_ssl=False,
            mail_require_tls=False,
            mail_allow_invalid_cert=False,
            smtp_username="",
            smtp_password="",
        )

    mail_server = _require("MAIL_SERVER", os.environ.get("MAIL_SERVER", ""))
    mail_port_str = _require("MAIL_PORT", os.environ.get("MAIL_PORT", ""))
    try:
        mail_port = int(mail_port_str)
    except ValueError as exc:
        raise RuntimeError("MAIL_PORT must be an integer.") from exc
    if mail_port <= 0:
        raise RuntimeError("MAIL_PORT must be positive.")

    mail_from = _require("MAIL_FROM", os.environ.get("MAIL_FROM", ""))
    mail_from_name = _require("MAIL_FROM_NAME", os.environ.get("MAIL_FROM_NAME", ""))
    mail_use_tls = _required_bool_env("MAIL_USE_TLS")
    mail_use_ssl = _required_bool_env("MAIL_USE_SSL")
    mail_require_tls = _required_bool_env("MAIL_REQUIRE_TLS")
    mail_allow_invalid_cert = _required_bool_env("MAIL_ALLOW_INVALID_CERT")

    if mail_use_tls and mail_use_ssl:
        raise RuntimeError("MAIL_USE_TLS and MAIL_USE_SSL cannot both be true.")
    if mail_require_tls and not mail_use_tls:
        raise RuntimeError("MAIL_REQUIRE_TLS requires MAIL_USE_TLS=true.")

    mail_username = (os.environ.get("MAIL_USERNAME", "") or "").strip()
    mail_password = (os.environ.get("MAIL_PASSWORD", "") or "").strip()
    sender_username = (os.environ.get("MAIL_SENDERNAME", "") or "").strip()
    sender_password = (os.environ.get("MAIL_SENDERPASSWORD", "") or "").strip()

    primary_complete = bool(mail_username and mail_password)
    primary_partial = bool(mail_username) ^ bool(mail_password)
    secondary_complete = bool(sender_username and sender_password)
    secondary_partial = bool(sender_username) ^ bool(sender_password)

    if primary_partial:
        raise RuntimeError("MAIL_USERNAME and MAIL_PASSWORD must be set as a pair.")
    if secondary_partial:
        raise RuntimeError(
            "MAIL_SENDERNAME and MAIL_SENDERPASSWORD must be set as a pair."
        )
    if not primary_complete and not secondary_complete:
        raise RuntimeError(
            "Set either MAIL_USERNAME+MAIL_PASSWORD or "
            "MAIL_SENDERNAME+MAIL_SENDERPASSWORD when MAIL_ENABLED is true."
        )

    smtp_username = mail_username if primary_complete else sender_username
    smtp_password = mail_password if primary_complete else sender_password

    return AuthFlowSettings(
        base_url=base_url,
        post_verify_redirect_path=post_verify_redirect_path,
        mail_enabled=True,
        mail_server=mail_server,
        mail_port=mail_port,
        mail_from=mail_from,
        mail_from_name=mail_from_name,
        mail_use_tls=mail_use_tls,
        mail_use_ssl=mail_use_ssl,
        mail_require_tls=mail_require_tls,
        mail_allow_invalid_cert=mail_allow_invalid_cert,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
    )


def validate_startup_settings() -> None:
    get_authflow_settings()
