from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from django.template.loader import render_to_string
from django.utils.html import strip_tags

from .env import get_authflow_settings

logger = logging.getLogger(__name__)


def _build_ssl_context(allow_invalid_cert: bool) -> ssl.SSLContext:
    if allow_invalid_cert:
        return ssl._create_unverified_context()
    return ssl.create_default_context()


def send_access_email(email: str, verify_url: str) -> bool:
    cfg = get_authflow_settings()
    if not cfg.mail_enabled:
        logger.info("authflow_mail_skip phase=disabled mail_enabled=false")
        return False

    html_body = render_to_string(
        "authflow/emails/access_link.html",
        {"verify_url": verify_url, "base_url": cfg.base_url},
    )
    text_body = strip_tags(html_body)

    msg = EmailMessage()
    msg["Subject"] = "Your sign-in link"
    msg["From"] = f"{cfg.mail_from_name} <{cfg.mail_from}>"
    msg["To"] = email
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    context = _build_ssl_context(cfg.mail_allow_invalid_cert)
    tls_active = False
    phase = "connect"
    connection_flags = (
        f"use_tls={cfg.mail_use_tls} use_ssl={cfg.mail_use_ssl} "
        f"require_tls={cfg.mail_require_tls} port={cfg.mail_port}"
    )

    try:
        if cfg.mail_use_ssl or cfg.mail_port == 465:
            phase = "connect_ssl"
            with smtplib.SMTP_SSL(
                cfg.mail_server, cfg.mail_port, context=context, timeout=15
            ) as server:
                tls_active = isinstance(server.sock, ssl.SSLSocket)
                phase = "login"
                if cfg.smtp_username and cfg.smtp_password:
                    server.login(cfg.smtp_username, cfg.smtp_password)
                if cfg.mail_require_tls and not tls_active:
                    raise RuntimeError("TLS required but SSL/TLS is not active.")
                phase = "send"
                server.send_message(msg)
        else:
            phase = "connect_plain"
            with smtplib.SMTP(cfg.mail_server, cfg.mail_port, timeout=15) as server:
                server.ehlo()
                if cfg.mail_use_tls:
                    phase = "starttls"
                    server.starttls(context=context)
                    server.ehlo()
                tls_active = isinstance(server.sock, ssl.SSLSocket)
                phase = "login"
                if cfg.smtp_username and cfg.smtp_password:
                    server.login(cfg.smtp_username, cfg.smtp_password)
                if cfg.mail_require_tls and not tls_active:
                    raise RuntimeError("TLS required but TLS is not active.")
                phase = "send"
                server.send_message(msg)
    except Exception:
        logger.exception(
            "authflow_mail_failed phase=%s tls_active=%s %s",
            phase,
            tls_active,
            connection_flags,
        )
        return False

    logger.info(
        "authflow_mail_sent phase=done tls_active=%s %s", tls_active, connection_flags
    )
    return True
