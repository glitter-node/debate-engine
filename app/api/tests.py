from __future__ import annotations

from ipaddress import ip_network

from django.contrib.auth import get_user_model
from django.test import TestCase
from unittest.mock import patch

from api.middleware.ip_block import IPBlocker, is_internal_ip, is_trusted_bot
from thinking.models import Argument, Counter, Thesis


class ApiEndpointTests(TestCase):
    def test_healthz_returns_ok_json(self):
        response = self.client.get("/api/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_root_endpoint_returns_ok_text(self):
        response = self.client.get("/api/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("OK", response.content.decode("utf-8"))

    def test_theses_default_returns_200_and_expected_shape(self):
        user = get_user_model().objects.create_user(
            username="apiuser1", password="pw-123456"
        )
        Thesis.objects.create(
            title="First thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=user,
        )

        response = self.client.get("/api/theses")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("page", data)
        self.assertIn("page_size", data)
        self.assertIn("num_pages", data)
        self.assertIn("count", data)
        self.assertIn("theses", data)
        self.assertEqual(data["page_size"], 20)
        self.assertIsInstance(data["theses"], list)
        if data["theses"]:
            first = data["theses"][0]
            self.assertIn("id", first)
            self.assertIn("title", first)
            self.assertIn("stance", first)
            self.assertIn("author", first)
            self.assertIn("counter_count", first)
            self.assertIn("created_at", first)
            self.assertIn("updated_at", first)

    def test_theses_invalid_sort_falls_back_to_active(self):
        user = get_user_model().objects.create_user(
            username="apiuser2", password="pw-123456"
        )
        thesis_a = Thesis.objects.create(
            title="A",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=user,
        )
        Thesis.objects.create(
            title="B",
            summary="summary",
            stance=Thesis.Stance.CON,
            author=user,
        )
        thesis_c = Thesis.objects.create(
            title="C",
            summary="summary",
            stance=Thesis.Stance.SUSPEND,
            author=user,
        )

        arg_a = Argument.objects.create(thesis=thesis_a, order=1, body="arg a")
        arg_c = Argument.objects.create(thesis=thesis_c, order=1, body="arg c")

        Counter.objects.create(
            thesis=thesis_a,
            target_argument=arg_a,
            body="counter a1",
            author=user,
        )
        Counter.objects.create(
            thesis=thesis_a,
            target_argument=arg_a,
            body="counter a2",
            author=user,
        )
        Counter.objects.create(
            thesis=thesis_c,
            target_argument=arg_c,
            body="counter c1",
            author=user,
        )

        weird_response = self.client.get("/api/theses?sort=weird&page=1")
        active_response = self.client.get("/api/theses?sort=active&page=1")

        self.assertEqual(weird_response.status_code, 200)
        self.assertEqual(active_response.status_code, 200)

        weird_data = weird_response.json()
        active_data = active_response.json()

        self.assertIn("page", weird_data)
        self.assertIn("page_size", weird_data)
        self.assertIn("num_pages", weird_data)
        self.assertIn("count", weird_data)
        self.assertIn("theses", weird_data)

        self.assertIn("page", active_data)
        self.assertIn("page_size", active_data)
        self.assertIn("num_pages", active_data)
        self.assertIn("count", active_data)
        self.assertIn("theses", active_data)

        weird_ids = [item["id"] for item in weird_data["theses"]]
        active_ids = [item["id"] for item in active_data["theses"]]
        self.assertEqual(weird_ids, active_ids)


class ApiMiddlewareHelperTests(TestCase):
    def test_is_internal_ip_uses_configured_ranges(self):
        with patch(
            "api.middleware.ip_block.const.INTERNAL_IP_RANGES",
            (ip_network("127.0.0.0/8"),),
        ):
            self.assertTrue(is_internal_ip("127.0.0.1"))
            self.assertFalse(is_internal_ip("203.0.113.10"))

    def test_is_trusted_bot_matches_user_agent(self):
        with patch("api.middleware.ip_block.const.TRUSTED_BOTS", ("Googlebot",)):
            self.assertTrue(is_trusted_bot("Mozilla/5.0 Googlebot/2.1"))
            self.assertFalse(is_trusted_bot("Mozilla/5.0 Safari"))

    def test_ip_blocker_stop_watcher_sets_stop_event(self):
        blocker = IPBlocker(path="/tmp/does-not-exist.json", interval=1)
        blocker.start_watcher_once()
        blocker.stop_watcher()
        self.assertTrue(blocker._stop_event.is_set())
