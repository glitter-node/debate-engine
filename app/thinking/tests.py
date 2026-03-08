from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from thinking.admin import CounterAdmin
from thinking.auto_moderation import maybe_auto_moderate_after_report
from thinking.audit import log_action
from thinking.content_status import ContentStatus
from thinking.moderation_metrics import (
    ESCALATION_LEVEL_1,
    ESCALATION_LEVEL_2,
    ESCALATION_LEVEL_3,
)
from thinking.models import Argument, AuditLog, ContentReport, Counter, Thesis, UserRole
from thinking.roles import user_has_site_role
from thinking.site_roles import SiteRole
from thinking.views import MAX_PRIORITY_SORT_CANDIDATES


class CounterInvariantTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="author", password="pw-123456"
        )
        self.thesis_a = Thesis.objects.create(
            title="A",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        self.thesis_b = Thesis.objects.create(
            title="B",
            summary="summary",
            stance=Thesis.Stance.CON,
            author=self.user,
        )
        self.argument_b = Argument.objects.create(
            thesis=self.thesis_b, order=1, body="arg b"
        )

    def test_counter_target_argument_must_match_thesis(self):
        counter = Counter(
            thesis=self.thesis_a,
            target_argument=self.argument_b,
            body="counter",
            author=self.user,
        )
        with self.assertRaises(ValidationError):
            counter.save()

    def test_content_status_defaults_to_active(self):
        self.assertEqual(self.thesis_a.status, ContentStatus.ACTIVE)
        arg_a = Argument.objects.create(thesis=self.thesis_a, order=1, body="arg a")
        counter = Counter.objects.create(
            thesis=self.thesis_a,
            target_argument=arg_a,
            body="counter a",
            author=self.user,
        )
        self.assertEqual(counter.status, ContentStatus.ACTIVE)

    def test_deleted_rows_are_hidden_by_default_manager(self):
        self.thesis_a.soft_delete(actor=self.user)
        self.assertFalse(Thesis.objects.filter(pk=self.thesis_a.pk).exists())
        self.assertTrue(Thesis.all_objects.filter(pk=self.thesis_a.pk).exists())


class ThinkingTemplateIntegrationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="author2", password="pw-123456"
        )
        for idx in range(25):
            Thesis.objects.create(
                title=f"Thesis {idx}",
                summary="summary",
                stance=Thesis.Stance.SUSPEND,
                author=self.user,
            )

    def test_pagination_preserves_sort_parameter(self):
        response = self.client.get(
            reverse("thinking:thesis_list"), {"sort": "new", "page": 2}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "sort=new&page=1")

    def test_base_template_has_favicon_fallback(self):
        response = self.client.get(reverse("thinking:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/favicon.ico"')


class CounterCreateLabelTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="counterlabeluser",
            password="pw-123456",
        )
        self.thesis = Thesis.objects.create(
            title="Counter Label Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        self.argument_one = Argument.objects.create(
            thesis=self.thesis,
            order=1,
            body="First argument body for dropdown label verification.",
        )
        self.argument_two = Argument.objects.create(
            thesis=self.thesis,
            order=2,
            body="Second argument body for dropdown label verification.",
        )
        self.argument_three = Argument.objects.create(
            thesis=self.thesis,
            order=3,
            body="Third argument body for dropdown label verification.",
        )

    def test_counter_create_dropdown_uses_a_order_labels(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("thinking:counter_create", kwargs={"pk": self.thesis.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Target argument")
        self.assertContains(response, "A1")
        self.assertContains(response, "A2")
        self.assertContains(response, "A3")
        self.assertNotContains(response, f"{self.thesis.pk}:1")
        self.assertNotContains(response, f"{self.thesis.pk}:2")
        self.assertNotContains(response, f"{self.thesis.pk}:3")

    def test_counter_create_post_creates_counter_and_redirects(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("thinking:counter_create", kwargs={"pk": self.thesis.pk}),
            {
                "target_argument": self.argument_two.pk,
                "body": "Counter body from test",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("thinking:thesis_detail", kwargs={"pk": self.thesis.pk}),
        )
        created = Counter.objects.get(
            thesis=self.thesis,
            target_argument=self.argument_two,
            author=self.user,
        )
        self.assertEqual(created.body, "Counter body from test")


class ArgumentAdminUxTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.superuser = user_model.objects.create_superuser(
            username="argadmin",
            email="argadmin@example.com",
            password="pw-123456",
        )
        self.thesis = Thesis.objects.create(
            title="Admin Context Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.superuser,
        )
        self.argument = Argument.objects.create(
            thesis=self.thesis,
            order=1,
            body="Argument body for admin context checks.",
        )
        self.factory = RequestFactory()

    def test_argument_admin_registration_has_context_columns(self):
        argument_admin = admin.site._registry[Argument]
        self.assertIn("thesis", argument_admin.list_display)
        self.assertIn("order", argument_admin.list_display)
        self.assertIn("short_body", argument_admin.list_display)
        self.assertIn("created_at", argument_admin.list_display)
        self.assertIn("thesis__title", argument_admin.search_fields)
        self.assertIn("body", argument_admin.search_fields)
        self.assertIn("thesis", argument_admin.list_filter)

    def test_counter_admin_target_argument_fk_label_includes_thesis_context(self):
        request = self.factory.get("/admin/thinking/counter/add/")
        request.user = self.superuser
        counter_admin = CounterAdmin(Counter, admin.site)
        form_field = counter_admin.formfield_for_foreignkey(
            Counter._meta.get_field("target_argument"),
            request,
        )
        labels = [label for _, label in form_field.choices]
        self.assertTrue(any("Admin Context Thesis" in label for label in labels))
        self.assertTrue(any("A1" in label for label in labels))


class AuthBoundaryTests(TestCase):
    def setUp(self):
        cache.clear()
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="normaluser",
            email="normal@example.com",
            password="pw-123456",
        )
        self.staff = user_model.objects.create_user(
            username="staffuser",
            email="staff@example.com",
            password="pw-123456",
            is_staff=True,
        )
        self.moderator = user_model.objects.create_user(
            username="moderatoruser",
            email="moderator@example.com",
            password="pw-123456",
        )
        self.operator = user_model.objects.create_user(
            username="operatoruser",
            email="operator@example.com",
            password="pw-123456",
        )
        UserRole.objects.create(user=self.moderator, role=SiteRole.MODERATOR)
        UserRole.objects.create(user=self.operator, role=SiteRole.OPERATOR)
        UserRole.objects.create(user=self.staff, role=SiteRole.USER)

    def test_protected_view_redirects_to_auth_for_anonymous(self):
        response = self.client.get(reverse("thinking:thesis_create"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/?next=", response["Location"])
        self.assertNotIn("/admin/login/", response["Location"])

    def test_non_staff_authenticated_user_can_access_normal_protected_view(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("thinking:thesis_create"))
        self.assertEqual(response.status_code, 200)

    def test_non_staff_cannot_access_admin(self):
        self.client.force_login(self.user)
        response = self.client.get("/admin/")
        self.assertIn(response.status_code, {302, 403})
        if response.status_code == 302:
            self.assertIn("/admin/login/", response["Location"])

    def test_staff_can_access_admin(self):
        self.client.force_login(self.staff)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)

    def test_nav_hides_admin_for_anon_and_non_staff(self):
        anon_response = self.client.get(reverse("thinking:home"))
        self.assertContains(anon_response, "Sign in")
        self.assertContains(anon_response, "Request access")
        self.assertNotContains(anon_response, ">Admin<")

        self.client.force_login(self.user)
        user_response = self.client.get(reverse("thinking:home"))
        self.assertContains(user_response, "Logout")
        self.assertNotContains(user_response, ">Admin<")

    def test_nav_shows_admin_for_staff(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("thinking:home"))
        self.assertContains(response, ">Admin<")

    def test_nav_shows_moderation_for_moderator_and_operator(self):
        self.client.force_login(self.moderator)
        moderator_response = self.client.get(reverse("thinking:home"))
        self.assertContains(moderator_response, ">Moderation<")

        self.client.force_login(self.operator)
        operator_response = self.client.get(reverse("thinking:home"))
        self.assertContains(operator_response, ">Moderation<")

    def test_nav_hides_moderation_for_basic_user(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("thinking:home"))
        self.assertNotContains(response, ">Moderation<")

    def test_user_role_default_created_lazily_and_blocks_moderation(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 403)
        self.user.refresh_from_db()
        self.assertEqual(self.user.site_role.role, SiteRole.USER)
        self.assertEqual(AuditLog.objects.filter(action="moderation.access").count(), 0)

    def test_moderation_panel_redirects_anonymous_to_auth(self):
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/?next=", response["Location"])
        self.assertIn("/moderation/", response["Location"])

    def test_moderator_role_can_access_moderation_panel(self):
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Moderation Panel")
        log_row = AuditLog.objects.get(action="moderation.access")
        self.assertEqual(log_row.actor_id, self.moderator.id)
        self.assertEqual(log_row.actor_role, SiteRole.MODERATOR)
        self.assertEqual(log_row.ip_address, "127.0.0.1")

    def test_moderation_panel_shows_open_count_aggregate_for_target(self):
        thesis = Thesis.objects.create(
            title="Aggregate Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        for idx in range(3):
            reporter = get_user_model().objects.create_user(
                username=f"mod_agg_{idx}",
                email=f"mod_agg_{idx}@example.com",
                password="pw-123456",
            )
            ContentReport.objects.create(
                reporter=reporter,
                reporter_role=SiteRole.USER,
                thesis=thesis,
                reason="spam",
            )
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "open 3")
        self.assertContains(response, "Auto-threshold")

    def test_moderation_panel_filters_target_type_reason_and_only_auto(self):
        thesis = Thesis.objects.create(
            title="Filter Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        argument = Argument.objects.create(thesis=thesis, order=1, body="arg")
        counter = Counter.objects.create(
            thesis=thesis,
            target_argument=argument,
            body="counter",
            author=self.user,
        )
        for idx in range(3):
            reporter = get_user_model().objects.create_user(
                username=f"mod_filter_t_{idx}",
                email=f"mod_filter_t_{idx}@example.com",
                password="pw-123456",
            )
            ContentReport.objects.create(
                reporter=reporter,
                reporter_role=SiteRole.USER,
                thesis=thesis,
                reason="spam",
            )
        counter_reporter = get_user_model().objects.create_user(
            username="mod_filter_counter",
            email="mod_filter_counter@example.com",
            password="pw-123456",
        )
        ContentReport.objects.create(
            reporter=counter_reporter,
            reporter_role=SiteRole.USER,
            counter=counter,
            reason="other",
        )
        self.client.force_login(self.moderator)

        target_type_response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"target_type": "counter"},
        )
        self.assertEqual(target_type_response.status_code, 200)
        self.assertContains(target_type_response, f"Counter #{counter.pk}")
        self.assertNotContains(target_type_response, thesis.title)

        reason_response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"reason": "other"},
        )
        self.assertEqual(reason_response.status_code, 200)
        self.assertContains(reason_response, f"Counter #{counter.pk}")
        self.assertNotContains(reason_response, thesis.title)

        only_auto_response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"only_auto": "1"},
        )
        self.assertEqual(only_auto_response.status_code, 200)
        self.assertContains(only_auto_response, thesis.title)
        self.assertNotContains(only_auto_response, f"Counter #{counter.pk}")

    def test_moderation_panel_marks_deleted_targets(self):
        thesis = Thesis.objects.create(
            title="Deleted Marker Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        thesis.soft_delete(actor=self.moderator)
        ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="spam",
        )
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deleted")

    def test_moderation_panel_query_count_is_bounded(self):
        thesis = Thesis.objects.create(
            title="Query Bound Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        for idx in range(6):
            reporter = get_user_model().objects.create_user(
                username=f"mod_q_{idx}",
                email=f"mod_q_{idx}@example.com",
                password="pw-123456",
            )
            ContentReport.objects.create(
                reporter=reporter,
                reporter_role=SiteRole.USER,
                thesis=thesis,
                reason="spam",
            )
        self.client.force_login(self.moderator)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 12)

    def test_metrics_tab_access_boundaries(self):
        anon_response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"tab": "metrics", "since_days": 30},
        )
        self.assertEqual(anon_response.status_code, 302)
        self.assertIn("/auth/?next=", anon_response["Location"])

        self.client.force_login(self.user)
        user_response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"tab": "metrics", "since_days": 30},
        )
        self.assertEqual(user_response.status_code, 403)

        self.client.force_login(self.moderator)
        mod_response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"tab": "metrics", "since_days": 30},
        )
        self.assertEqual(mod_response.status_code, 200)
        self.assertContains(mod_response, "Moderator Decision Metrics")

    def test_metrics_tab_shows_status_counts_latency_operator_and_hot_targets(self):
        now = timezone.now()
        thesis = Thesis.objects.create(
            title="Metrics Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        old_reporter = get_user_model().objects.create_user(
            username="metrics_old",
            email="metrics_old@example.com",
            password="pw-123456",
        )
        old_report = ContentReport.objects.create(
            reporter=old_reporter,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="other",
            status=ContentReport.Status.OPEN,
        )
        ContentReport.objects.filter(pk=old_report.pk).update(
            created_at=now - timedelta(days=45)
        )

        open_reporters = []
        for idx in range(3):
            reporter = get_user_model().objects.create_user(
                username=f"metrics_open_{idx}",
                email=f"metrics_open_{idx}@example.com",
                password="pw-123456",
            )
            open_reporters.append(reporter)
        open_reports = [
            ContentReport.objects.create(
                reporter=reporter,
                reporter_role=SiteRole.USER,
                thesis=thesis,
                reason="spam",
                status=ContentReport.Status.OPEN,
            )
            for reporter in open_reporters
        ]
        for report in open_reports:
            ContentReport.objects.filter(pk=report.pk).update(
                created_at=now - timedelta(days=1)
            )

        resolved_reporters = []
        for idx in range(3):
            reporter = get_user_model().objects.create_user(
                username=f"metrics_resolved_{idx}",
                email=f"metrics_resolved_{idx}@example.com",
                password="pw-123456",
            )
            resolved_reporters.append(reporter)
        resolved_report_a = ContentReport.objects.create(
            reporter=resolved_reporters[0],
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="hate",
            status=ContentReport.Status.RESOLVED,
            resolved_by=self.moderator,
            resolved_at=now - timedelta(days=1),
        )
        resolved_report_b = ContentReport.objects.create(
            reporter=resolved_reporters[1],
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="harassment",
            status=ContentReport.Status.RESOLVED,
            resolved_by=self.operator,
            resolved_at=now - timedelta(days=1),
        )
        dismissed_report = ContentReport.objects.create(
            reporter=resolved_reporters[2],
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="other",
            status=ContentReport.Status.DISMISSED,
            resolved_by=self.moderator,
            resolved_at=now - timedelta(days=1),
        )
        ContentReport.objects.filter(pk=resolved_report_a.pk).update(
            created_at=now - timedelta(days=1, minutes=10),
            resolved_at=now - timedelta(days=1),
        )
        ContentReport.objects.filter(pk=resolved_report_b.pk).update(
            created_at=now - timedelta(days=1, minutes=20),
            resolved_at=now - timedelta(days=1),
        )
        ContentReport.objects.filter(pk=dismissed_report.pk).update(
            created_at=now - timedelta(days=1, hours=1),
            resolved_at=now - timedelta(days=1),
        )

        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"tab": "metrics", "since_days": 30},
        )
        self.assertEqual(response.status_code, 200)
        metrics = response.context["metrics"]
        self.assertEqual(metrics["counts_by_status"]["open_count"], 3)
        self.assertEqual(metrics["counts_by_status"]["resolved_count"], 2)
        self.assertEqual(metrics["counts_by_status"]["dismissed_count"], 1)
        self.assertEqual(metrics["latency"]["median_seconds"], 1200)
        self.assertEqual(metrics["latency"]["p90_seconds"], 3600)
        operator_counts = {
            row["operator_username"]: row["decision_count"]
            for row in metrics["operator_decisions"]
        }
        self.assertEqual(operator_counts[self.moderator.username], 2)
        self.assertEqual(operator_counts[self.operator.username], 1)
        operator_workload = {
            row["operator_username"]: row for row in metrics["operator_metrics"]
        }
        self.assertEqual(
            operator_workload[self.moderator.username]["decisions_count"], 2
        )
        self.assertEqual(
            operator_workload[self.moderator.username]["resolved_count"], 1
        )
        self.assertEqual(
            operator_workload[self.moderator.username]["dismissed_count"], 1
        )
        self.assertEqual(
            operator_workload[self.moderator.username]["median_seconds"], 600
        )
        self.assertEqual(
            operator_workload[self.moderator.username]["p90_seconds"], 3600
        )
        self.assertEqual(
            operator_workload[self.operator.username]["decisions_count"], 1
        )
        self.assertEqual(
            operator_workload[self.operator.username]["median_seconds"], 1200
        )
        self.assertFalse(metrics["operator_not_found"])
        self.assertEqual(metrics["hot_targets_count"], 1)
        self.assertEqual(metrics["hot_reports_total"], 3)
        self.assertContains(response, "(1200s)")
        self.assertContains(response, "(3600s)")
        self.assertContains(response, self.moderator.username)
        self.assertContains(response, self.operator.username)
        self.assertContains(response, "Hot targets (open_count")
        self.assertContains(response, "Open reports on hot targets: 3")
        self.assertContains(response, "Operator Workload (window: 30 days)")
        self.assertContains(response, self.moderator.username)
        self.assertContains(response, self.operator.username)

    def test_metrics_tab_operator_filter_scopes_operator_workload(self):
        now = timezone.now().replace(microsecond=0)
        thesis = Thesis.objects.create(
            title="Metrics Operator Filter Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        report_one = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="spam",
            status=ContentReport.Status.RESOLVED,
            resolved_by=self.moderator,
            resolved_at=now - timedelta(hours=2),
        )
        report_two = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="other",
            status=ContentReport.Status.DISMISSED,
            resolved_by=self.operator,
            resolved_at=now - timedelta(hours=1),
        )
        ContentReport.objects.filter(pk=report_one.pk).update(
            created_at=now - timedelta(hours=2, minutes=20),
            resolved_at=now - timedelta(hours=2),
        )
        ContentReport.objects.filter(pk=report_two.pk).update(
            created_at=now - timedelta(hours=1, minutes=10),
            resolved_at=now - timedelta(hours=1),
        )
        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"tab": "metrics", "since_days": 30, "operator": self.operator.username},
        )
        self.assertEqual(response.status_code, 200)
        metrics = response.context["metrics"]
        self.assertTrue(metrics["operator_filter_applied"])
        self.assertEqual(metrics["operator_filter"], self.operator.username)
        self.assertFalse(metrics["operator_not_found"])
        self.assertEqual(len(metrics["operator_metrics"]), 1)
        only_row = metrics["operator_metrics"][0]
        self.assertEqual(only_row["operator_username"], self.operator.username)
        self.assertEqual(only_row["decisions_count"], 1)
        self.assertEqual(only_row["dismissed_count"], 1)
        self.assertEqual(only_row["resolved_count"], 0)

    def test_metrics_tab_operator_filter_not_found_note(self):
        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"tab": "metrics", "since_days": 30, "operator": "doesnotexist"},
        )
        self.assertEqual(response.status_code, 200)
        metrics = response.context["metrics"]
        self.assertTrue(metrics["operator_filter_applied"])
        self.assertTrue(metrics["operator_not_found"])
        self.assertEqual(metrics["operator_filter"], "doesnotexist")
        self.assertEqual(metrics["operator_metrics"], [])
        self.assertContains(response, "No decisions found for operator in window.")

    def test_metrics_csv_export_access_boundaries(self):
        anon_response = self.client.get(reverse("thinking:moderation_metrics_csv"))
        self.assertEqual(anon_response.status_code, 302)
        self.assertIn("/auth/?next=", anon_response["Location"])

        self.client.force_login(self.user)
        user_response = self.client.get(reverse("thinking:moderation_metrics_csv"))
        self.assertEqual(user_response.status_code, 403)
        self.assertEqual(user_response["Content-Type"], "text/plain; charset=utf-8")

        self.client.force_login(self.moderator)
        mod_response = self.client.get(reverse("thinking:moderation_metrics_csv"))
        self.assertEqual(mod_response.status_code, 200)
        self.assertTrue(mod_response["Content-Type"].startswith("text/csv"))

    def test_metrics_csv_export_headers_and_content(self):
        fixed_now = timezone.now().replace(microsecond=0)
        thesis = Thesis.objects.create(
            title="CSV Metrics Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        resolved_report = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="spam",
            status=ContentReport.Status.RESOLVED,
            resolved_by=self.moderator,
            resolved_at=fixed_now - timedelta(hours=1),
        )
        dismissed_report = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="other",
            status=ContentReport.Status.DISMISSED,
            resolved_by=self.operator,
            resolved_at=fixed_now - timedelta(hours=1),
        )
        open_report = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="harassment",
            status=ContentReport.Status.OPEN,
        )
        ContentReport.objects.filter(pk=resolved_report.pk).update(
            created_at=fixed_now - timedelta(hours=1, minutes=20),
            resolved_at=fixed_now - timedelta(hours=1),
        )
        ContentReport.objects.filter(pk=dismissed_report.pk).update(
            created_at=fixed_now - timedelta(hours=1, minutes=10),
            resolved_at=fixed_now - timedelta(hours=1),
        )
        ContentReport.objects.filter(pk=open_report.pk).update(
            created_at=fixed_now - timedelta(hours=2)
        )

        self.client.force_login(self.moderator)
        with patch("thinking.views.timezone.now", return_value=fixed_now):
            response = self.client.get(
                reverse("thinking:moderation_metrics_csv"),
                {"since_days": 30},
            )
        self.assertEqual(response.status_code, 200)
        content_disposition = response["Content-Disposition"]
        self.assertIn("attachment", content_disposition)
        self.assertIn("moderation_metrics_30d_", content_disposition)
        csv_body = response.content.decode("utf-8")
        self.assertIn("Status counts", csv_body)
        self.assertIn("open_count,1", csv_body)
        self.assertIn("resolved_count,1", csv_body)
        self.assertIn("dismissed_count,1", csv_body)
        self.assertIn("latency_median_seconds", csv_body)
        self.assertIn("latency_p90_seconds", csv_body)
        self.assertIn(self.moderator.username, csv_body)
        self.assertIn(self.operator.username, csv_body)
        self.assertIn("Operator workload", csv_body)

    def test_metrics_csv_export_operator_filter(self):
        fixed_now = timezone.now().replace(microsecond=0)
        thesis = Thesis.objects.create(
            title="CSV Metrics Filter Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        one = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="spam",
            status=ContentReport.Status.RESOLVED,
            resolved_by=self.moderator,
            resolved_at=fixed_now - timedelta(minutes=30),
        )
        two = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="other",
            status=ContentReport.Status.DISMISSED,
            resolved_by=self.operator,
            resolved_at=fixed_now - timedelta(minutes=20),
        )
        ContentReport.objects.filter(pk=one.pk).update(
            created_at=fixed_now - timedelta(minutes=50),
            resolved_at=fixed_now - timedelta(minutes=30),
        )
        ContentReport.objects.filter(pk=two.pk).update(
            created_at=fixed_now - timedelta(minutes=50),
            resolved_at=fixed_now - timedelta(minutes=20),
        )
        self.client.force_login(self.moderator)
        with patch("thinking.views.timezone.now", return_value=fixed_now):
            response = self.client.get(
                reverse("thinking:moderation_metrics_csv"),
                {"since_days": 30, "operator": self.operator.username},
            )
        self.assertEqual(response.status_code, 200)
        csv_body = response.content.decode("utf-8")
        self.assertIn(self.operator.username, csv_body)
        self.assertNotIn(self.moderator.username, csv_body)

    def test_metrics_tab_stale_open_summary(self):
        now = timezone.now()
        thesis = Thesis.objects.create(
            title="Stale Metrics Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        stale_reporter = get_user_model().objects.create_user(
            username="stale_metrics_old",
            email="stale_metrics_old@example.com",
            password="pw-123456",
        )
        fresh_reporter = get_user_model().objects.create_user(
            username="stale_metrics_new",
            email="stale_metrics_new@example.com",
            password="pw-123456",
        )
        stale_report = ContentReport.objects.create(
            reporter=stale_reporter,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="spam",
            status=ContentReport.Status.OPEN,
        )
        fresh_report = ContentReport.objects.create(
            reporter=fresh_reporter,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="other",
            status=ContentReport.Status.OPEN,
        )
        ContentReport.objects.filter(pk=stale_report.pk).update(
            created_at=now - timedelta(hours=72)
        )
        ContentReport.objects.filter(pk=fresh_report.pk).update(
            created_at=now - timedelta(hours=6)
        )
        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"tab": "metrics", "since_days": 30},
        )
        self.assertEqual(response.status_code, 200)
        metrics = response.context["metrics"]
        self.assertEqual(metrics["stale_threshold_hours"], 48)
        self.assertEqual(metrics["stale_open_count"], 1)
        self.assertIsNotNone(metrics["oldest_stale_open_age_seconds"])
        self.assertGreaterEqual(metrics["oldest_stale_open_age_seconds"], 71 * 3600)
        self.assertLessEqual(metrics["oldest_stale_open_age_seconds"], 73 * 3600)

    def test_metrics_tab_includes_escalation_distribution_and_max_level(self):
        level_0_thesis = Thesis.objects.create(
            title="Escalation L0 Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        level_1_thesis = Thesis.objects.create(
            title="Escalation L1 Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        level_2_thesis = Thesis.objects.create(
            title="Escalation L2 Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        level_3_thesis = Thesis.objects.create(
            title="Escalation L3 Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        for thesis, report_count, prefix in (
            (level_0_thesis, 2, "escalation_l0"),
            (level_1_thesis, ESCALATION_LEVEL_1, "escalation_l1"),
            (level_2_thesis, ESCALATION_LEVEL_2, "escalation_l2"),
            (level_3_thesis, ESCALATION_LEVEL_3, "escalation_l3"),
        ):
            for idx in range(report_count):
                reporter = get_user_model().objects.create_user(
                    username=f"{prefix}_{idx}",
                    email=f"{prefix}_{idx}@example.com",
                    password="pw-123456",
                )
                ContentReport.objects.create(
                    reporter=reporter,
                    reporter_role=SiteRole.USER,
                    thesis=thesis,
                    reason="spam",
                    status=ContentReport.Status.OPEN,
                )
        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"tab": "metrics", "since_days": 30},
        )
        self.assertEqual(response.status_code, 200)
        metrics = response.context["metrics"]
        self.assertEqual(metrics["escalation_distribution"]["level_1_targets"], 3)
        self.assertEqual(metrics["escalation_distribution"]["level_2_targets"], 2)
        self.assertEqual(metrics["escalation_distribution"]["level_3_targets"], 1)
        self.assertEqual(metrics["max_escalation_level"], 3)
        self.assertContains(response, "Level 1 targets: 3")
        self.assertContains(response, "Level 2 targets: 2")
        self.assertContains(response, "Level 3 targets: 1")
        self.assertContains(response, "Highest escalation level active: 3")

    def test_reports_tab_shows_stale_badge_for_stale_only(self):
        now = timezone.now()
        stale_thesis = Thesis.objects.create(
            title="Stale Badge Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        fresh_thesis = Thesis.objects.create(
            title="Fresh Badge Thesis",
            summary="summary",
            stance=Thesis.Stance.CON,
            author=self.user,
        )
        stale_report = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=stale_thesis,
            reason="spam",
            status=ContentReport.Status.OPEN,
        )
        fresh_report = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=fresh_thesis,
            reason="other",
            status=ContentReport.Status.OPEN,
        )
        ContentReport.objects.filter(pk=stale_report.pk).update(
            created_at=now - timedelta(hours=60)
        )
        ContentReport.objects.filter(pk=fresh_report.pk).update(
            created_at=now - timedelta(hours=6)
        )
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "STALE")
        self.assertContains(response, stale_thesis.title)
        self.assertContains(response, fresh_thesis.title)

    def test_reports_tab_shows_escalation_badges_and_boundaries(self):
        theses = {
            "l0": Thesis.objects.create(
                title="Esc Reports L0",
                summary="summary",
                stance=Thesis.Stance.PRO,
                author=self.user,
            ),
            "l1": Thesis.objects.create(
                title="Esc Reports L1",
                summary="summary",
                stance=Thesis.Stance.PRO,
                author=self.user,
            ),
            "l2": Thesis.objects.create(
                title="Esc Reports L2",
                summary="summary",
                stance=Thesis.Stance.PRO,
                author=self.user,
            ),
            "l3": Thesis.objects.create(
                title="Esc Reports L3",
                summary="summary",
                stance=Thesis.Stance.PRO,
                author=self.user,
            ),
        }
        for key, report_count in (
            ("l0", ESCALATION_LEVEL_1 - 1),
            ("l1", ESCALATION_LEVEL_1),
            ("l2", ESCALATION_LEVEL_2),
            ("l3", ESCALATION_LEVEL_3),
        ):
            for idx in range(report_count):
                reporter = get_user_model().objects.create_user(
                    username=f"esc_reports_{key}_{idx}",
                    email=f"esc_reports_{key}_{idx}@example.com",
                    password="pw-123456",
                )
                ContentReport.objects.create(
                    reporter=reporter,
                    reporter_role=SiteRole.USER,
                    thesis=theses[key],
                    reason="spam",
                    status=ContentReport.Status.OPEN,
                )
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 200)
        rows_by_title = {
            row["target_label"]: row
            for row in response.context["open_reports"]
            if row["report"].target_type == ContentReport.TargetType.THESIS
        }
        self.assertEqual(rows_by_title["Esc Reports L0"]["escalation_level"], 0)
        self.assertEqual(rows_by_title["Esc Reports L1"]["escalation_level"], 1)
        self.assertEqual(rows_by_title["Esc Reports L2"]["escalation_level"], 2)
        self.assertEqual(rows_by_title["Esc Reports L3"]["escalation_level"], 3)
        self.assertContains(response, "ESC L1")
        self.assertContains(response, "ESC L2")
        self.assertContains(response, "ESC L3")

    def test_escalation_level_filter_limits_targets_by_minimum_tier(self):
        low_thesis = Thesis.objects.create(
            title="Esc Filter Low",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        level_two_thesis = Thesis.objects.create(
            title="Esc Filter L2",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        level_three_thesis = Thesis.objects.create(
            title="Esc Filter L3",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        for thesis, count, prefix in (
            (low_thesis, ESCALATION_LEVEL_1, "esc_filter_low"),
            (level_two_thesis, ESCALATION_LEVEL_2, "esc_filter_l2"),
            (level_three_thesis, ESCALATION_LEVEL_3, "esc_filter_l3"),
        ):
            for idx in range(count):
                reporter = get_user_model().objects.create_user(
                    username=f"{prefix}_{idx}",
                    email=f"{prefix}_{idx}@example.com",
                    password="pw-123456",
                )
                ContentReport.objects.create(
                    reporter=reporter,
                    reporter_role=SiteRole.USER,
                    thesis=thesis,
                    reason="spam",
                    status=ContentReport.Status.OPEN,
                )
        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"target_type": "thesis", "escalation_level": "2"},
        )
        self.assertEqual(response.status_code, 200)
        titles = {
            row["target_label"]
            for row in response.context["open_reports"]
            if row["report"].target_type == ContentReport.TargetType.THESIS
        }
        self.assertNotIn(low_thesis.title, titles)
        self.assertIn(level_two_thesis.title, titles)
        self.assertIn(level_three_thesis.title, titles)

    def test_reports_row_can_show_stale_and_escalation_badges_together(self):
        now = timezone.now()
        thesis = Thesis.objects.create(
            title="Esc Stale Combined",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        report_ids = []
        for idx in range(ESCALATION_LEVEL_1):
            reporter = get_user_model().objects.create_user(
                username=f"esc_stale_{idx}",
                email=f"esc_stale_{idx}@example.com",
                password="pw-123456",
            )
            report = ContentReport.objects.create(
                reporter=reporter,
                reporter_role=SiteRole.USER,
                thesis=thesis,
                reason="spam",
                status=ContentReport.Status.OPEN,
            )
            report_ids.append(report.id)
        ContentReport.objects.filter(pk=report_ids[0]).update(
            created_at=now - timedelta(hours=60)
        )
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 200)
        matching_rows = [
            row
            for row in response.context["open_reports"]
            if row["target_label"] == thesis.title
        ]
        self.assertTrue(any(row["is_stale"] for row in matching_rows))
        self.assertTrue(any(row["escalation_level"] >= 1 for row in matching_rows))
        self.assertContains(response, "STALE")
        self.assertContains(response, "ESC L1")

    def test_priority_sort_orders_by_escalation_first(self):
        now = timezone.now()
        low_thesis = Thesis.objects.create(
            title="Priority Low Esc",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        high_thesis = Thesis.objects.create(
            title="Priority High Esc",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        for idx in range(3):
            ContentReport.objects.create(
                reporter=get_user_model().objects.create_user(
                    username=f"priority_low_{idx}",
                    email=f"priority_low_{idx}@example.com",
                    password="pw-123456",
                ),
                reporter_role=SiteRole.USER,
                thesis=low_thesis,
                reason="spam",
                status=ContentReport.Status.OPEN,
            )
        for idx in range(10):
            ContentReport.objects.create(
                reporter=get_user_model().objects.create_user(
                    username=f"priority_high_{idx}",
                    email=f"priority_high_{idx}@example.com",
                    password="pw-123456",
                ),
                reporter_role=SiteRole.USER,
                thesis=high_thesis,
                reason="spam",
                status=ContentReport.Status.OPEN,
            )
        ContentReport.objects.filter(thesis_id=low_thesis.pk).update(
            created_at=now - timedelta(hours=2)
        )
        ContentReport.objects.filter(thesis_id=high_thesis.pk).update(
            created_at=now - timedelta(hours=1)
        )
        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:moderation_panel"), {"sort": "priority"}
        )
        self.assertEqual(response.status_code, 200)
        first_row = response.context["open_reports"][0]
        self.assertEqual(first_row["target_label"], high_thesis.title)
        self.assertEqual(first_row["escalation_level"], 3)

    def test_priority_sort_prefers_stale_within_same_escalation(self):
        now = timezone.now()
        stale_thesis = Thesis.objects.create(
            title="Priority Stale L2",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        fresh_thesis = Thesis.objects.create(
            title="Priority Fresh L2",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        for idx in range(5):
            ContentReport.objects.create(
                reporter=get_user_model().objects.create_user(
                    username=f"priority_stale_{idx}",
                    email=f"priority_stale_{idx}@example.com",
                    password="pw-123456",
                ),
                reporter_role=SiteRole.USER,
                thesis=stale_thesis,
                reason="spam",
                status=ContentReport.Status.OPEN,
            )
            ContentReport.objects.create(
                reporter=get_user_model().objects.create_user(
                    username=f"priority_fresh_{idx}",
                    email=f"priority_fresh_{idx}@example.com",
                    password="pw-123456",
                ),
                reporter_role=SiteRole.USER,
                thesis=fresh_thesis,
                reason="spam",
                status=ContentReport.Status.OPEN,
            )
        ContentReport.objects.filter(thesis_id=stale_thesis.pk).update(
            created_at=now - timedelta(hours=60)
        )
        ContentReport.objects.filter(thesis_id=fresh_thesis.pk).update(
            created_at=now - timedelta(hours=2)
        )
        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:moderation_panel"), {"sort": "priority"}
        )
        self.assertEqual(response.status_code, 200)
        first_row = response.context["open_reports"][0]
        self.assertEqual(first_row["target_label"], stale_thesis.title)
        self.assertTrue(first_row["is_stale"])
        self.assertEqual(first_row["escalation_level"], 2)

    def test_priority_sort_uses_oldest_first_as_tiebreaker(self):
        now = timezone.now()
        older_thesis = Thesis.objects.create(
            title="Priority Oldest",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        newer_thesis = Thesis.objects.create(
            title="Priority Newer",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        for idx in range(5):
            ContentReport.objects.create(
                reporter=get_user_model().objects.create_user(
                    username=f"priority_old_{idx}",
                    email=f"priority_old_{idx}@example.com",
                    password="pw-123456",
                ),
                reporter_role=SiteRole.USER,
                thesis=older_thesis,
                reason="spam",
                status=ContentReport.Status.OPEN,
            )
            ContentReport.objects.create(
                reporter=get_user_model().objects.create_user(
                    username=f"priority_new_{idx}",
                    email=f"priority_new_{idx}@example.com",
                    password="pw-123456",
                ),
                reporter_role=SiteRole.USER,
                thesis=newer_thesis,
                reason="spam",
                status=ContentReport.Status.OPEN,
            )
        ContentReport.objects.filter(thesis_id=older_thesis.pk).update(
            created_at=now - timedelta(hours=70)
        )
        ContentReport.objects.filter(thesis_id=newer_thesis.pk).update(
            created_at=now - timedelta(hours=55)
        )
        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:moderation_panel"), {"sort": "priority"}
        )
        self.assertEqual(response.status_code, 200)
        first_row = response.context["open_reports"][0]
        self.assertEqual(first_row["target_label"], older_thesis.title)
        self.assertTrue(first_row["is_stale"])
        self.assertEqual(first_row["escalation_level"], 2)

    def test_default_sort_order_is_unchanged_without_priority(self):
        older_thesis = Thesis.objects.create(
            title="Default Older",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        newer_thesis = Thesis.objects.create(
            title="Default Newer",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        older_report = ContentReport.objects.create(
            reporter=get_user_model().objects.create_user(
                username="default_older_user",
                email="default_older_user@example.com",
                password="pw-123456",
            ),
            reporter_role=SiteRole.USER,
            thesis=older_thesis,
            reason="spam",
            status=ContentReport.Status.OPEN,
        )
        newer_report = ContentReport.objects.create(
            reporter=get_user_model().objects.create_user(
                username="default_newer_user",
                email="default_newer_user@example.com",
                password="pw-123456",
            ),
            reporter_role=SiteRole.USER,
            thesis=newer_thesis,
            reason="spam",
            status=ContentReport.Status.OPEN,
        )
        now = timezone.now()
        ContentReport.objects.filter(pk=older_report.pk).update(
            created_at=now - timedelta(hours=5)
        )
        ContentReport.objects.filter(pk=newer_report.pk).update(
            created_at=now - timedelta(hours=1)
        )
        self.client.force_login(self.moderator)
        default_response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(default_response.status_code, 200)
        first_default = default_response.context["open_reports"][0]
        self.assertEqual(first_default["target_label"], newer_thesis.title)

    def test_priority_sort_cap_is_stable_for_large_filtered_set(self):
        thesis = Thesis.objects.create(
            title="Priority Cap Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        reports = [
            ContentReport(
                reporter=None,
                reporter_role="",
                thesis=thesis,
                reason="spam",
                status=ContentReport.Status.OPEN,
            )
            for _ in range(MAX_PRIORITY_SORT_CANDIDATES + 5)
        ]
        ContentReport.objects.bulk_create(reports)
        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:moderation_panel"), {"sort": "priority"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["priority_mode"])
        self.assertTrue(response.context["priority_candidates_capped"])
        self.assertEqual(
            response.context["open_reports_count"], MAX_PRIORITY_SORT_CANDIDATES
        )
        self.assertContains(response, "Priority mode capped to oldest")

    def test_stale_only_filter_limits_to_stale_open(self):
        now = timezone.now()
        stale_thesis = Thesis.objects.create(
            title="Stale Filter Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        fresh_thesis = Thesis.objects.create(
            title="Fresh Filter Thesis",
            summary="summary",
            stance=Thesis.Stance.CON,
            author=self.user,
        )
        resolved_thesis = Thesis.objects.create(
            title="Resolved Old Thesis",
            summary="summary",
            stance=Thesis.Stance.SUSPEND,
            author=self.user,
        )
        stale_open = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=stale_thesis,
            reason="spam",
            status=ContentReport.Status.OPEN,
        )
        fresh_open = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=fresh_thesis,
            reason="other",
            status=ContentReport.Status.OPEN,
        )
        resolved_old = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=resolved_thesis,
            reason="hate",
            status=ContentReport.Status.RESOLVED,
            resolved_by=self.moderator,
            resolved_at=now - timedelta(hours=1),
        )
        ContentReport.objects.filter(pk=stale_open.pk).update(
            created_at=now - timedelta(hours=72)
        )
        ContentReport.objects.filter(pk=fresh_open.pk).update(
            created_at=now - timedelta(hours=4)
        )
        ContentReport.objects.filter(pk=resolved_old.pk).update(
            created_at=now - timedelta(hours=72),
            resolved_at=now - timedelta(hours=1),
        )
        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"stale_only": "1"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, stale_thesis.title)
        self.assertNotContains(response, fresh_thesis.title)
        self.assertNotContains(response, resolved_thesis.title)

    def test_stale_boundary_created_at_equals_cutoff_is_not_stale(self):
        fixed_now = timezone.now().replace(microsecond=0)
        thesis = Thesis.objects.create(
            title="Boundary Stale Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        edge_report = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="spam",
            status=ContentReport.Status.OPEN,
        )
        ContentReport.objects.filter(pk=edge_report.pk).update(
            created_at=fixed_now - timedelta(hours=48)
        )
        self.client.force_login(self.moderator)
        with patch("thinking.views.timezone.now", return_value=fixed_now):
            response = self.client.get(
                reverse("thinking:moderation_panel"),
                {"stale_only": "1"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, thesis.title)

    def test_operator_role_can_access_moderation_panel(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Moderation Panel")
        log_row = AuditLog.objects.get(action="moderation.access")
        self.assertEqual(log_row.actor_id, self.operator.id)
        self.assertEqual(log_row.actor_role, SiteRole.OPERATOR)

    def test_staff_flag_without_site_role_permission_cannot_access_moderation(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AuditLog.objects.filter(action="moderation.access").count(), 0)

    def test_role_helper_accepts_case_insensitive_role_strings(self):
        self.assertTrue(user_has_site_role(self.moderator, "MODERATOR"))
        self.assertTrue(user_has_site_role(self.operator, "operator"))

    def test_moderation_action_post_creates_audit_row(self):
        self.client.force_login(self.moderator)
        response = self.client.post(reverse("thinking:moderation_mark_reviewed"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("thinking:moderation_panel"))
        self.assertTrue(
            AuditLog.objects.filter(
                action="moderation.mark_reviewed", actor_id=self.moderator.id
            ).exists()
        )

    def test_user_cannot_change_thesis_status(self):
        thesis = Thesis.objects.create(
            title="Status Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("thinking:moderation_thesis_set_status", kwargs={"pk": thesis.pk}),
            {"status": ContentStatus.ARCHIVED},
        )
        self.assertEqual(response.status_code, 403)
        thesis.refresh_from_db()
        self.assertEqual(thesis.status, ContentStatus.ACTIVE)

    def test_moderator_can_change_thesis_status_and_audit_is_written(self):
        thesis = Thesis.objects.create(
            title="Status Thesis 2",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        self.client.force_login(self.moderator)
        response = self.client.post(
            reverse("thinking:moderation_thesis_set_status", kwargs={"pk": thesis.pk}),
            {"status": ContentStatus.ARCHIVED},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("thinking:thesis_detail", kwargs={"pk": thesis.pk}),
        )
        thesis.refresh_from_db()
        self.assertEqual(thesis.status, ContentStatus.ARCHIVED)
        log_row = AuditLog.objects.filter(action="moderation.status_change").latest(
            "id"
        )
        self.assertEqual(log_row.actor_id, self.moderator.id)
        self.assertEqual(log_row.metadata.get("old_status"), ContentStatus.ACTIVE)
        self.assertEqual(log_row.metadata.get("new_status"), ContentStatus.ARCHIVED)

    def test_non_moderator_does_not_see_archived_thesis_in_list(self):
        Thesis.objects.create(
            title="Visible Active",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
            status=ContentStatus.ACTIVE,
        )
        Thesis.objects.create(
            title="Hidden Archived",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
            status=ContentStatus.ARCHIVED,
        )
        response = self.client.get(reverse("thinking:thesis_list"))
        self.assertContains(response, "Visible Active")
        self.assertNotContains(response, "Hidden Archived")

    def test_moderator_can_include_inactive_in_list(self):
        Thesis.objects.create(
            title="Visible Active Mod",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
            status=ContentStatus.ACTIVE,
        )
        Thesis.objects.create(
            title="Visible Archived Mod",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
            status=ContentStatus.ARCHIVED,
        )
        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:thesis_list"), {"include_inactive": 1}
        )
        self.assertContains(response, "Visible Active Mod")
        self.assertContains(response, "Visible Archived Mod")

    def test_deleted_thesis_hidden_from_normal_user_list_and_detail(self):
        deleted = Thesis.objects.create(
            title="Hidden Deleted",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        deleted.soft_delete(actor=self.moderator)
        list_response = self.client.get(reverse("thinking:thesis_list"))
        self.assertNotContains(list_response, "Hidden Deleted")

        detail_response = self.client.get(
            reverse("thinking:thesis_detail", kwargs={"pk": deleted.pk})
        )
        self.assertEqual(detail_response.status_code, 404)

    def test_moderator_can_include_deleted_in_list(self):
        visible = Thesis.objects.create(
            title="Visible Active Deleted Toggle",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        deleted = Thesis.objects.create(
            title="Visible Deleted Mod",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        deleted.soft_delete(actor=self.moderator)
        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:thesis_list"), {"include_deleted": 1}
        )
        self.assertContains(response, visible.title)
        self.assertContains(response, deleted.title)

    def test_user_cannot_soft_delete_or_restore(self):
        thesis = Thesis.objects.create(
            title="Delete Blocked",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        self.client.force_login(self.user)
        delete_response = self.client.post(
            reverse("thinking:moderation_thesis_delete", kwargs={"pk": thesis.pk})
        )
        self.assertEqual(delete_response.status_code, 403)
        thesis.refresh_from_db()
        self.assertFalse(thesis.is_deleted)

        thesis.soft_delete(actor=self.moderator)
        restore_response = self.client.post(
            reverse("thinking:moderation_thesis_restore", kwargs={"pk": thesis.pk})
        )
        self.assertEqual(restore_response.status_code, 403)
        thesis.refresh_from_db()
        self.assertTrue(thesis.is_deleted)

    def test_moderator_can_soft_delete_and_restore_thesis_with_audit(self):
        thesis = Thesis.objects.create(
            title="Delete Allowed",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        self.client.force_login(self.moderator)
        delete_response = self.client.post(
            reverse("thinking:moderation_thesis_delete", kwargs={"pk": thesis.pk})
        )
        self.assertEqual(delete_response.status_code, 302)
        thesis.refresh_from_db()
        self.assertTrue(thesis.is_deleted)
        self.assertEqual(thesis.deleted_by_id, self.moderator.id)
        delete_log = AuditLog.objects.filter(action="moderation.soft_delete").latest(
            "id"
        )
        self.assertEqual(delete_log.actor_id, self.moderator.id)
        self.assertEqual(delete_log.metadata.get("status"), thesis.status)
        self.assertEqual(delete_log.metadata.get("id"), str(thesis.id))

        restore_response = self.client.post(
            reverse("thinking:moderation_thesis_restore", kwargs={"pk": thesis.pk})
        )
        self.assertEqual(restore_response.status_code, 302)
        thesis.refresh_from_db()
        self.assertFalse(thesis.is_deleted)
        self.assertIsNone(thesis.deleted_by_id)
        restore_log = AuditLog.objects.filter(action="moderation.restore").latest("id")
        self.assertEqual(restore_log.actor_id, self.moderator.id)
        self.assertEqual(restore_log.metadata.get("id"), str(thesis.id))

    def test_moderator_can_soft_delete_and_restore_counter_with_audit(self):
        thesis = Thesis.objects.create(
            title="Counter Delete Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        argument = Argument.objects.create(thesis=thesis, order=1, body="arg")
        counter = Counter.objects.create(
            thesis=thesis,
            target_argument=argument,
            body="counter",
            author=self.user,
        )
        self.client.force_login(self.moderator)
        delete_response = self.client.post(
            reverse("thinking:moderation_counter_delete", kwargs={"pk": counter.pk})
        )
        self.assertEqual(delete_response.status_code, 302)
        counter.refresh_from_db()
        self.assertTrue(counter.is_deleted)
        self.assertEqual(counter.deleted_by_id, self.moderator.id)
        self.assertTrue(
            AuditLog.objects.filter(action="moderation.soft_delete").exists()
        )

        restore_response = self.client.post(
            reverse("thinking:moderation_counter_restore", kwargs={"pk": counter.pk})
        )
        self.assertEqual(restore_response.status_code, 302)
        counter.refresh_from_db()
        self.assertFalse(counter.is_deleted)
        self.assertIsNone(counter.deleted_by_id)
        self.assertTrue(AuditLog.objects.filter(action="moderation.restore").exists())

    def test_authenticated_user_can_report_thesis_and_audit_written(self):
        thesis = Thesis.objects.create(
            title="Reportable Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}),
            {"reason": "spam", "detail": "looks like spam"},
        )
        self.assertEqual(response.status_code, 302)
        report = ContentReport.objects.get(
            reporter=self.user,
            thesis=thesis,
        )
        self.assertEqual(report.status, ContentReport.Status.OPEN)
        self.assertEqual(report.reason, "spam")
        self.assertTrue(
            AuditLog.objects.filter(
                action="content.report_submitted",
                metadata__target_type=ContentReport.TargetType.THESIS,
                metadata__target_id=str(thesis.pk),
            ).exists()
        )

    def test_duplicate_open_report_is_blocked(self):
        thesis = Thesis.objects.create(
            title="Duplicate Report Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        self.client.force_login(self.user)
        first = self.client.post(
            reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}),
            {"reason": "other", "detail": "first"},
        )
        second = self.client.post(
            reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}),
            {"reason": "other", "detail": "second"},
        )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            ContentReport.objects.filter(
                reporter=self.user,
                thesis=thesis,
                status=ContentReport.Status.OPEN,
            ).count(),
            1,
        )

    def test_deleted_target_cannot_be_reported(self):
        thesis = Thesis.objects.create(
            title="Deleted Thesis Report Block",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        thesis.soft_delete(actor=self.moderator)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}),
            {"reason": "spam"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            ContentReport.objects.filter(
                thesis=thesis,
            ).exists()
        )

    def test_report_rate_limit_blocks_after_three_per_minute(self):
        theses = [
            Thesis.objects.create(
                title=f"Rate Report {idx}",
                summary="summary",
                stance=Thesis.Stance.PRO,
                author=self.user,
            )
            for idx in range(4)
        ]
        self.client.force_login(self.user)
        for thesis in theses:
            response = self.client.post(
                reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}),
                {"reason": "other"},
            )
            self.assertEqual(response.status_code, 302)
        self.assertEqual(
            ContentReport.objects.filter(
                reporter=self.user,
                status=ContentReport.Status.OPEN,
                thesis__isnull=False,
            ).count(),
            3,
        )

    def test_auto_moderation_sets_pending_review_at_three_reports_for_thesis(self):
        thesis = Thesis.objects.create(
            title="Auto Pending Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        reporters = []
        for idx in range(3):
            reporter = get_user_model().objects.create_user(
                username=f"auto_pending_{idx}",
                email=f"auto_pending_{idx}@example.com",
                password="pw-123456",
            )
            reporters.append(reporter)
        for reporter in reporters:
            self.client.force_login(reporter)
            response = self.client.post(
                reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}),
                {"reason": "spam"},
            )
            self.assertEqual(response.status_code, 302)
        thesis.refresh_from_db()
        self.assertEqual(thesis.status, ContentStatus.PENDING_REVIEW)
        auto_logs = AuditLog.objects.filter(
            action="moderation.auto_status_change",
            metadata__target_type=ContentReport.TargetType.THESIS,
            metadata__target_id=str(thesis.pk),
        )
        self.assertEqual(auto_logs.count(), 1)
        row = auto_logs.latest("id")
        self.assertEqual(row.actor_id, None)
        self.assertEqual(row.metadata.get("old_status"), ContentStatus.ACTIVE)
        self.assertEqual(row.metadata.get("new_status"), ContentStatus.PENDING_REVIEW)
        self.assertEqual(row.metadata.get("threshold_triggered"), 3)
        self.assertEqual(row.metadata.get("open_report_count"), 3)

    def test_auto_moderation_upgrades_to_archived_at_five_reports_for_thesis(self):
        thesis = Thesis.objects.create(
            title="Auto Archived Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        for idx in range(5):
            reporter = get_user_model().objects.create_user(
                username=f"auto_archived_{idx}",
                email=f"auto_archived_{idx}@example.com",
                password="pw-123456",
            )
            self.client.force_login(reporter)
            response = self.client.post(
                reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}),
                {"reason": "spam"},
            )
            self.assertEqual(response.status_code, 302)
        thesis.refresh_from_db()
        self.assertEqual(thesis.status, ContentStatus.ARCHIVED)
        auto_logs = AuditLog.objects.filter(
            action="moderation.auto_status_change",
            metadata__target_type=ContentReport.TargetType.THESIS,
            metadata__target_id=str(thesis.pk),
        ).order_by("id")
        self.assertEqual(auto_logs.count(), 2)
        self.assertEqual(
            list(auto_logs.values_list("metadata__threshold_triggered", flat=True)),
            [3, 5],
        )
        self.assertEqual(
            auto_logs[0].metadata.get("new_status"), ContentStatus.PENDING_REVIEW
        )
        self.assertEqual(
            auto_logs[1].metadata.get("new_status"), ContentStatus.ARCHIVED
        )

    def test_auto_moderation_is_idempotent_for_duplicate_open_report(self):
        thesis = Thesis.objects.create(
            title="Auto Idempotent Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        reporters = []
        for idx in range(3):
            reporter = get_user_model().objects.create_user(
                username=f"auto_idempotent_{idx}",
                email=f"auto_idempotent_{idx}@example.com",
                password="pw-123456",
            )
            reporters.append(reporter)
        for reporter in reporters:
            self.client.force_login(reporter)
            self.client.post(
                reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}),
                {"reason": "spam"},
            )
        thesis.refresh_from_db()
        self.assertEqual(thesis.status, ContentStatus.PENDING_REVIEW)
        self.assertEqual(
            AuditLog.objects.filter(action="moderation.auto_status_change").count(), 1
        )
        self.client.force_login(reporters[0])
        duplicate = self.client.post(
            reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}),
            {"reason": "spam"},
        )
        self.assertEqual(duplicate.status_code, 302)
        thesis.refresh_from_db()
        self.assertEqual(thesis.status, ContentStatus.PENDING_REVIEW)
        self.assertEqual(
            AuditLog.objects.filter(action="moderation.auto_status_change").count(), 1
        )

    def test_auto_moderation_helper_returns_false_for_deleted_target(self):
        thesis = Thesis.objects.create(
            title="Auto Deleted Guard",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        thesis.soft_delete(actor=self.moderator)
        changed = maybe_auto_moderate_after_report(
            target=thesis,
            request=None,
        )
        self.assertFalse(changed)
        thesis.refresh_from_db()
        self.assertEqual(thesis.status, ContentStatus.ACTIVE)
        self.assertFalse(
            AuditLog.objects.filter(action="moderation.auto_status_change").exists()
        )

    def test_auto_moderation_does_not_change_rejected_target(self):
        thesis = Thesis.objects.create(
            title="Auto Rejected Guard",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
            status=ContentStatus.REJECTED,
        )
        for idx in range(5):
            reporter = get_user_model().objects.create_user(
                username=f"auto_rejected_{idx}",
                email=f"auto_rejected_{idx}@example.com",
                password="pw-123456",
            )
            self.client.force_login(reporter)
            response = self.client.post(
                reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}),
                {"reason": "spam"},
            )
            self.assertEqual(response.status_code, 302)
        thesis.refresh_from_db()
        self.assertEqual(thesis.status, ContentStatus.REJECTED)
        self.assertFalse(
            AuditLog.objects.filter(
                action="moderation.auto_status_change",
                metadata__target_type=ContentReport.TargetType.THESIS,
                metadata__target_id=str(thesis.pk),
            ).exists()
        )

    def test_auto_moderation_sets_pending_review_for_counter(self):
        thesis = Thesis.objects.create(
            title="Auto Counter Parent",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        argument = Argument.objects.create(thesis=thesis, order=1, body="arg")
        counter = Counter.objects.create(
            thesis=thesis,
            target_argument=argument,
            body="counter",
            author=self.user,
        )
        for idx in range(3):
            reporter = get_user_model().objects.create_user(
                username=f"auto_counter_{idx}",
                email=f"auto_counter_{idx}@example.com",
                password="pw-123456",
            )
            self.client.force_login(reporter)
            response = self.client.post(
                reverse("thinking:report_counter", kwargs={"pk": counter.pk}),
                {"reason": "spam"},
            )
            self.assertEqual(response.status_code, 302)
        counter.refresh_from_db()
        self.assertEqual(counter.status, ContentStatus.PENDING_REVIEW)
        self.assertTrue(
            AuditLog.objects.filter(
                action="moderation.auto_status_change",
                metadata__target_type=ContentReport.TargetType.COUNTER,
                metadata__target_id=str(counter.pk),
                metadata__threshold_triggered=3,
            ).exists()
        )

    def test_moderator_bulk_resolve_updates_open_reports_and_logs_each(self):
        thesis_a = Thesis.objects.create(
            title="Bulk Resolve A",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        thesis_b = Thesis.objects.create(
            title="Bulk Resolve B",
            summary="summary",
            stance=Thesis.Stance.CON,
            author=self.user,
        )
        argument = Argument.objects.create(thesis=thesis_a, order=1, body="arg")
        counter = Counter.objects.create(
            thesis=thesis_a,
            target_argument=argument,
            body="counter",
            author=self.user,
        )
        reporter_a = get_user_model().objects.create_user(
            username="bulk_resolve_a",
            email="bulk_resolve_a@example.com",
            password="pw-123456",
        )
        reporter_b = get_user_model().objects.create_user(
            username="bulk_resolve_b",
            email="bulk_resolve_b@example.com",
            password="pw-123456",
        )
        reporter_c = get_user_model().objects.create_user(
            username="bulk_resolve_c",
            email="bulk_resolve_c@example.com",
            password="pw-123456",
        )
        report_one = ContentReport.objects.create(
            reporter=reporter_a,
            reporter_role=SiteRole.USER,
            thesis=thesis_a,
            reason="spam",
        )
        report_two = ContentReport.objects.create(
            reporter=reporter_b,
            reporter_role=SiteRole.USER,
            counter=counter,
            reason="other",
        )
        report_three = ContentReport.objects.create(
            reporter=reporter_c,
            reporter_role=SiteRole.USER,
            thesis=thesis_b,
            reason="hate",
        )
        self.client.force_login(self.moderator)
        response = self.client.post(
            reverse("thinking:moderation_reports_bulk"),
            {
                "action": "resolve",
                "report_ids": [
                    str(report_one.id),
                    str(report_two.id),
                    str(report_three.id),
                ],
                "next_query": "status=open&target_type=thesis&page=2",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            f"{reverse('thinking:moderation_panel')}?status=open&target_type=thesis&page=2",
        )
        report_one.refresh_from_db()
        report_two.refresh_from_db()
        report_three.refresh_from_db()
        self.assertEqual(report_one.status, ContentReport.Status.RESOLVED)
        self.assertEqual(report_two.status, ContentReport.Status.RESOLVED)
        self.assertEqual(report_three.status, ContentReport.Status.RESOLVED)
        self.assertEqual(report_one.resolved_by_id, self.moderator.id)
        self.assertEqual(report_two.resolved_by_id, self.moderator.id)
        self.assertEqual(report_three.resolved_by_id, self.moderator.id)
        self.assertEqual(
            AuditLog.objects.filter(
                action="content.report_resolved",
                metadata__report_id__in=[report_one.id, report_two.id, report_three.id],
            ).count(),
            3,
        )

    def test_operator_bulk_dismiss_updates_open_reports_and_logs_each(self):
        thesis = Thesis.objects.create(
            title="Bulk Dismiss Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        reporters = []
        for idx in range(2):
            reporter = get_user_model().objects.create_user(
                username=f"bulk_dismiss_{idx}",
                email=f"bulk_dismiss_{idx}@example.com",
                password="pw-123456",
            )
            reporters.append(reporter)
        reports = [
            ContentReport.objects.create(
                reporter=reporters[0],
                reporter_role=SiteRole.USER,
                thesis=thesis,
                reason="spam",
            ),
            ContentReport.objects.create(
                reporter=reporters[1],
                reporter_role=SiteRole.USER,
                thesis=thesis,
                reason="other",
            ),
        ]
        self.client.force_login(self.operator)
        response = self.client.post(
            reverse("thinking:moderation_reports_bulk"),
            {
                "action": "dismiss",
                "report_ids": [str(report.id) for report in reports],
                "next_query": "status=open",
            },
        )
        self.assertEqual(response.status_code, 302)
        for report in reports:
            report.refresh_from_db()
            self.assertEqual(report.status, ContentReport.Status.DISMISSED)
            self.assertEqual(report.resolved_by_id, self.operator.id)
        self.assertEqual(
            AuditLog.objects.filter(
                action="content.report_dismissed",
                metadata__report_id__in=[report.id for report in reports],
            ).count(),
            2,
        )

    def test_bulk_resolve_skips_non_open_reports_idempotently(self):
        thesis = Thesis.objects.create(
            title="Bulk Skip Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        reporters = []
        for idx in range(3):
            reporter = get_user_model().objects.create_user(
                username=f"bulk_skip_{idx}",
                email=f"bulk_skip_{idx}@example.com",
                password="pw-123456",
            )
            reporters.append(reporter)
        reports = [
            ContentReport.objects.create(
                reporter=reporters[idx],
                reporter_role=SiteRole.USER,
                thesis=thesis,
                reason="spam",
            )
            for idx in range(3)
        ]
        self.client.force_login(self.moderator)
        single_response = self.client.post(
            reverse("thinking:moderation_report_resolve", kwargs={"pk": reports[0].pk})
        )
        self.assertEqual(single_response.status_code, 302)
        before_bulk_logs = AuditLog.objects.filter(
            action="content.report_resolved"
        ).count()
        bulk_response = self.client.post(
            reverse("thinking:moderation_reports_bulk"),
            {
                "action": "resolve",
                "report_ids": [str(report.id) for report in reports],
                "next_query": "status=open",
            },
        )
        self.assertEqual(bulk_response.status_code, 302)
        for report in reports:
            report.refresh_from_db()
            self.assertEqual(report.status, ContentReport.Status.RESOLVED)
        after_bulk_logs = AuditLog.objects.filter(
            action="content.report_resolved"
        ).count()
        self.assertEqual(after_bulk_logs - before_bulk_logs, 2)

    def test_non_moderator_cannot_use_bulk_report_endpoint(self):
        thesis = Thesis.objects.create(
            title="Bulk Forbidden Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        report = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="spam",
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("thinking:moderation_reports_bulk"),
            {"action": "resolve", "report_ids": [str(report.id)]},
        )
        self.assertEqual(response.status_code, 403)
        report.refresh_from_db()
        self.assertEqual(report.status, ContentReport.Status.OPEN)

    def test_non_moderator_cannot_resolve_or_dismiss_report(self):
        thesis = Thesis.objects.create(
            title="Resolve Block Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        report = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="spam",
        )
        self.client.force_login(self.user)
        resolve_response = self.client.post(
            reverse("thinking:moderation_report_resolve", kwargs={"pk": report.pk})
        )
        dismiss_response = self.client.post(
            reverse("thinking:moderation_report_dismiss", kwargs={"pk": report.pk})
        )
        self.assertEqual(resolve_response.status_code, 403)
        self.assertEqual(dismiss_response.status_code, 403)
        report.refresh_from_db()
        self.assertEqual(report.status, ContentReport.Status.OPEN)

    def test_moderator_can_resolve_and_dismiss_reports_with_audit(self):
        thesis = Thesis.objects.create(
            title="Resolve Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        thesis_two = Thesis.objects.create(
            title="Dismiss Thesis",
            summary="summary",
            stance=Thesis.Stance.PRO,
            author=self.user,
        )
        resolve_report = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=thesis,
            reason="spam",
        )
        dismiss_report = ContentReport.objects.create(
            reporter=self.user,
            reporter_role=SiteRole.USER,
            thesis=thesis_two,
            reason="other",
        )
        self.client.force_login(self.moderator)
        resolve_response = self.client.post(
            reverse(
                "thinking:moderation_report_resolve", kwargs={"pk": resolve_report.pk}
            )
        )
        dismiss_response = self.client.post(
            reverse(
                "thinking:moderation_report_dismiss", kwargs={"pk": dismiss_report.pk}
            )
        )
        self.assertEqual(resolve_response.status_code, 302)
        self.assertEqual(dismiss_response.status_code, 302)
        resolve_report.refresh_from_db()
        dismiss_report.refresh_from_db()
        self.assertEqual(resolve_report.status, ContentReport.Status.RESOLVED)
        self.assertEqual(resolve_report.resolved_by_id, self.moderator.id)
        self.assertIsNotNone(resolve_report.resolved_at)
        self.assertEqual(dismiss_report.status, ContentReport.Status.DISMISSED)
        self.assertEqual(dismiss_report.resolved_by_id, self.moderator.id)
        self.assertTrue(
            AuditLog.objects.filter(
                action="content.report_resolved",
                metadata__report_id=resolve_report.id,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="content.report_dismissed",
                metadata__report_id=dismiss_report.id,
            ).exists()
        )

    def test_audit_log_survives_user_delete_with_null_actor(self):
        row = log_action(actor=self.user, action="test.user_event", metadata={"k": "v"})
        self.assertEqual(row.actor_id, self.user.id)
        self.user.delete()
        row.refresh_from_db()
        self.assertIsNone(row.actor_id)

    def test_log_action_with_anonymous_actor_stores_null_actor(self):
        row = log_action(actor=None, action="test.anonymous_event", metadata={"k": "v"})
        self.assertIsNone(row.actor_id)
        self.assertEqual(row.actor_role, "")


class AuditAdminTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_superuser(
            username="auditadmin",
            email="auditadmin@example.com",
            password="pw-123456",
        )
        self.normal_user = user_model.objects.create_user(
            username="plainuser",
            email="plainuser@example.com",
            password="pw-123456",
        )
        self.audit_row = AuditLog.objects.create(
            actor=self.admin_user,
            actor_role=SiteRole.OPERATOR,
            action="audit.seed",
            target_model="auth.user",
            target_id=str(self.admin_user.id),
            metadata={"source": "test"},
            ip_address="127.0.0.1",
            user_agent="test-agent",
        )

    def test_staff_can_access_audit_admin_changelist(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin:thinking_auditlog_changelist"))
        self.assertEqual(response.status_code, 200)

    def test_non_staff_cannot_access_audit_admin_changelist(self):
        self.client.force_login(self.normal_user)
        response = self.client.get(reverse("admin:thinking_auditlog_changelist"))
        self.assertIn(response.status_code, {302, 403})

    def test_audit_admin_disallows_add_and_change_posts(self):
        self.client.force_login(self.admin_user)
        add_response = self.client.post(
            reverse("admin:thinking_auditlog_add"),
            {"action": "audit.try_add"},
        )
        self.assertIn(add_response.status_code, {302, 403, 405})

        change_response = self.client.post(
            reverse("admin:thinking_auditlog_change", args=[self.audit_row.pk]),
            {"action": "audit.try_change"},
        )
        self.assertIn(change_response.status_code, {302, 403, 405})
