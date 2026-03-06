from __future__ import annotations

import json
import os
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone

from authflow.env import get_authflow_settings
from authflow.models import EmailAuthToken, GoogleAccountLink
from authflow.google_oauth import GoogleTokenVerificationError
from authflow.tokens import issue_email_key
from thinking.models import AuditLog
from thinking.site_roles import SiteRole


class AuthFlowTests(TestCase):
    def setUp(self):
        self.env_patcher = patch.dict(
            os.environ,
            {
                "MAIL_ENABLED": "true",
                "BASE_URL": "https://testserver",
                "MAIL_SERVER": "smtp.test.local",
                "MAIL_PORT": "587",
                "MAIL_FROM": "noreply@example.com",
                "MAIL_FROM_NAME": "Proto8",
                "MAIL_USE_TLS": "true",
                "MAIL_USE_SSL": "false",
                "MAIL_REQUIRE_TLS": "true",
                "MAIL_ALLOW_INVALID_CERT": "false",
                "MAIL_USERNAME": "smtp-user",
                "MAIL_PASSWORD": "smtp-pass",
                "POST_VERIFY_REDIRECT_PATH": "/",
                "GOOGLE_CLIENT_ID": "google-client-id.apps.googleusercontent.com",
            },
            clear=False,
        )
        self.env_patcher.start()
        self.original_google_client_id = settings.GOOGLE_CLIENT_ID
        settings.GOOGLE_CLIENT_ID = "google-client-id.apps.googleusercontent.com"
        get_authflow_settings.cache_clear()

    def tearDown(self):
        settings.GOOGLE_CLIENT_ID = self.original_google_client_id
        get_authflow_settings.cache_clear()
        self.env_patcher.stop()

    @patch("authflow.views.send_access_email", return_value=True)
    def test_request_access_creates_token_and_returns_generic_response(self, mock_send):
        response = self.client.post("/auth/request", {"email": "User@Example.com"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/auth/?sent=1")
        self.assertEqual(EmailAuthToken.objects.count(), 1)
        token_obj = EmailAuthToken.objects.first()
        self.assertEqual(token_obj.email, "user@example.com")

        verify_url = mock_send.call_args.kwargs["verify_url"]
        self.assertIn("k=", verify_url)
        self.assertNotIn("token=", verify_url)

        page = self.client.get("/auth/?sent=1")
        self.assertContains(
            page, "If the address is eligible, a sign-in link will be emailed shortly."
        )

    @patch("authflow.views.send_access_email", return_value=True)
    def test_verify_success_marks_token_used_creates_user_and_logs_in(self, _mock_send):
        key = issue_email_key("verify@example.com", request_ip="127.0.0.1")
        response = self.client.get(f"/auth/verify?k={key}")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

        token_obj = EmailAuthToken.objects.get(email="verify@example.com")
        self.assertIsNotNone(token_obj.used_at)

        user = get_user_model().objects.get(email__iexact="verify@example.com")
        self.assertEqual(str(self.client.session.get("_auth_user_id")), str(user.id))
        self.assertEqual(user.site_role.role, SiteRole.USER)
        audit_row = AuditLog.objects.get(action="auth.login_via_email")
        self.assertEqual(audit_row.actor_id, user.id)
        self.assertEqual(audit_row.target_model, "auth.user")
        self.assertEqual(audit_row.target_id, str(user.id))

    @patch("authflow.views.send_access_email", return_value=True)
    def test_verify_fails_on_reuse(self, _mock_send):
        key = issue_email_key("reuse@example.com")
        first = self.client.get(f"/auth/verify?k={key}")
        second = self.client.get(f"/auth/verify?k={key}")

        self.assertEqual(first.status_code, 302)
        self.assertEqual(first["Location"], "/")
        self.assertEqual(second.status_code, 302)
        self.assertEqual(second["Location"], "/auth/?invalid=1")

    @patch("authflow.views.send_access_email", return_value=True)
    def test_verify_fails_on_expired_token(self, _mock_send):
        key = issue_email_key("expired@example.com")
        token_obj = EmailAuthToken.objects.get(email="expired@example.com")
        token_obj.expires_at = timezone.now() - timedelta(minutes=1)
        token_obj.save(update_fields=["expires_at"])

        response = self.client.get(f"/auth/verify?k={key}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/auth/?invalid=1")

    @patch("authflow.views.send_access_email", return_value=True)
    def test_rate_limit_blocks_excessive_requests_with_generic_response(
        self, _mock_send
    ):
        for _ in range(5):
            response = self.client.post(
                "/auth/request", {"email": "limit@example.com"}, follow=True
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(
                response,
                "If the address is eligible, a sign-in link will be emailed shortly.",
            )

        self.assertLessEqual(EmailAuthToken.objects.count(), 3)

    def test_email_template_uses_k_link_not_token_link(self):
        html = render_to_string(
            "authflow/emails/access_link.html",
            {"verify_url": "https://testserver/auth/verify?k=abc123xyz456"},
        )
        self.assertIn("k=abc123xyz456", html)
        self.assertNotIn("token=", html)

    @patch(
        "authflow.views.verify_google_id_token",
        return_value={"sub": "sub-123", "email": "onetap@example.com"},
    )
    def test_google_onetap_creates_user_link_and_session(self, _mock_verify):
        response = self.client.post(
            "/auth/google/onetap",
            data=json.dumps({"credential": "jwt-value"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)
        self.assertEqual(response.json()["next"], "/theses/new/")

        user = get_user_model().objects.get(email__iexact="onetap@example.com")
        self.assertEqual(str(self.client.session.get("_auth_user_id")), str(user.id))
        self.assertEqual(user.site_role.role, SiteRole.USER)
        link = GoogleAccountLink.objects.get(google_sub="sub-123")
        self.assertEqual(link.user_id, user.id)
        self.assertEqual(link.email, "onetap@example.com")
        self.assertTrue(
            AuditLog.objects.filter(action="auth.login_via_google").exists()
        )

    @patch(
        "authflow.views.verify_google_id_token",
        return_value={"sub": "sub-456", "email": "existing@example.com"},
    )
    def test_google_onetap_reuses_existing_email_user(self, _mock_verify):
        existing = get_user_model().objects.create_user(
            username="existing-user",
            email="existing@example.com",
        )
        response = self.client.post(
            "/auth/google/onetap",
            data=json.dumps({"credential": "jwt-value"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)
        link = GoogleAccountLink.objects.get(google_sub="sub-456")
        self.assertEqual(link.user_id, existing.id)

    @patch(
        "authflow.views.verify_google_id_token",
        side_effect=GoogleTokenVerificationError("invalid"),
    )
    def test_google_onetap_rejects_invalid_google_token(self, _mock_verify):
        response = self.client.post(
            "/auth/google/onetap",
            data=json.dumps({"credential": "invalid"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["ok"], False)

    @patch(
        "authflow.views.verify_google_id_token",
        return_value={"sub": "sub-789", "email": "limited@example.com"},
    )
    @patch("authflow.views.allow_google_onetap_request", return_value=False)
    def test_google_onetap_rate_limit_returns_429(self, _mock_rl, _mock_verify):
        response = self.client.post(
            "/auth/google/onetap",
            data=json.dumps({"credential": "jwt-value"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["ok"], False)
