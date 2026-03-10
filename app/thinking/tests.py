from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import ForeignKey
from django.forms import ModelChoiceField
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from thinking.admin import CounterAdmin
from thinking.audit import log_action
from thinking.auto_moderation import maybe_auto_moderate_after_report
from thinking.content_status import ContentStatus
from thinking.domain.argument_service import calculate_claim_score, merge_claims
from thinking.models import (
    Argument,
    AuditLog,
    Claim,
    ClaimAlias,
    ClaimCanonical,
    ClaimContradiction,
    ClaimDuplicateReview,
    ClaimEntity,
    ClaimEvidence,
    ClaimInference,
    ClaimInferenceRule,
    ClaimMergeLog,
    ClaimNormalized,
    ClaimPredicate,
    ClaimRelation,
    ClaimRelationType,
    ClaimRevision,
    ClaimScore,
    ClaimSimilarity,
    ClaimSupportClosure,
    ClaimTriple,
    ClaimVote,
    ContentReport,
    Counter,
    DebateClaimMapping,
    Thesis,
    UserRole,
)
from thinking.moderation_metrics import (
    ESCALATION_LEVEL_1,
    ESCALATION_LEVEL_2,
    ESCALATION_LEVEL_3,
)
from thinking.roles import user_has_site_role
from thinking.services.claim_duplicates import (
    compute_cosine_similarity,
    generate_claim_embedding,
    review_duplicate_pair,
)
from thinking.services.claim_inference import rebuild_thesis_inference_safe
from thinking.services.claim_normalization import (
    canonicalize_entity_name,
    normalize_claim,
    parse_claim_text_to_triple,
)
from thinking.site_roles import SiteRole
from thinking.views import MAX_PRIORITY_SORT_CANDIDATES


class TestDataMixin:
    user_seq = 0

    @classmethod
    def next_username(cls, prefix: str) -> str:
        cls.user_seq += 1
        return f"{prefix}_{cls.user_seq}"

    def make_user(
        self,
        prefix: str = "user",
        *,
        password: str = "pw-123456",
        is_staff: bool = False,
        superuser: bool = False,
        username: str | None = None,
        email: str | None = None,
    ):
        user_model = get_user_model()
        actual_username = username or self.next_username(prefix)
        actual_email = email or f"{actual_username}@example.com"
        if superuser:
            return user_model.objects.create_superuser(
                username=actual_username,
                email=actual_email,
                password=password,
            )
        return user_model.objects.create_user(
            username=actual_username,
            email=actual_email,
            password=password,
            is_staff=is_staff,
        )

    def make_thesis(
        self,
        *,
        title: str,
        author,
        summary: str = "summary",
        stance=Thesis.Stance.PRO,
        status: str = ContentStatus.ACTIVE,
    ) -> Thesis:
        return Thesis.objects.create(
            title=title,
            summary=summary,
            stance=stance,
            author=author,
            status=status,
        )

    def make_argument(
        self,
        thesis: Thesis,
        *,
        order: int,
        body: str,
    ) -> Argument:
        return Argument.objects.create(thesis=thesis, order=order, body=body)

    def make_counter(
        self,
        thesis: Thesis,
        argument: Argument,
        *,
        body: str,
        author,
        parent: Counter | None = None,
    ) -> Counter:
        return Counter.objects.create(
            thesis=thesis,
            target_argument=argument,
            parent_counter=parent,
            body=body,
            author=author,
        )

    def make_claim(self, thesis: Thesis, *, author, body: str) -> Claim:
        return Claim.objects.create(thesis=thesis, author=author, body=body)

    def make_report(
        self,
        *,
        reporter,
        thesis: Thesis | None = None,
        counter: Counter | None = None,
        reason: str = "spam",
        status: str = ContentReport.Status.OPEN,
        reporter_role: str = SiteRole.USER,
        resolved_by=None,
        resolved_at=None,
    ) -> ContentReport:
        return ContentReport.objects.create(
            reporter=reporter,
            reporter_role=reporter_role,
            thesis=thesis,
            counter=counter,
            reason=reason,
            status=status,
            resolved_by=resolved_by,
            resolved_at=resolved_at,
        )

    def make_reports_for_thesis(
        self,
        thesis: Thesis,
        count: int,
        *,
        reason: str = "spam",
        prefix: str,
        status: str = ContentReport.Status.OPEN,
        resolved_by=None,
        resolved_at=None,
    ) -> list[ContentReport]:
        reports: list[ContentReport] = []
        for idx in range(count):
            reporter = self.make_user(prefix=prefix, username=f"{prefix}_{idx}")
            reports.append(
                self.make_report(
                    reporter=reporter,
                    thesis=thesis,
                    reason=reason,
                    status=status,
                    resolved_by=resolved_by,
                    resolved_at=resolved_at,
                )
            )
        return reports

    def get_context(self, response) -> dict[str, Any]:
        assert response.context is not None
        return response.context

    def get_response_header(self, response, key: str) -> str:
        typed_response = cast(HttpResponse, response)
        return typed_response[key]

    def set_report_time(
        self,
        report: ContentReport,
        *,
        created_at=None,
        resolved_at=None,
    ) -> None:
        updates: dict[str, Any] = {}
        if created_at is not None:
            updates["created_at"] = created_at
        if resolved_at is not None:
            updates["resolved_at"] = resolved_at
        if updates:
            ContentReport.objects.filter(pk=report.pk).update(**updates)


class CounterInvariantTests(TestDataMixin, TestCase):
    def setUp(self):
        self.user = self.make_user(prefix="author", username="author")
        self.thesis_a = self.make_thesis(title="A", author=self.user, stance=Thesis.Stance.PRO)
        self.thesis_b = self.make_thesis(title="B", author=self.user, stance=Thesis.Stance.CON)
        self.argument_b = self.make_argument(self.thesis_b, order=1, body="arg b")

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
        arg_a = self.make_argument(self.thesis_a, order=1, body="arg a")
        counter = self.make_counter(
            self.thesis_a,
            arg_a,
            body="counter a",
            author=self.user,
        )
        self.assertEqual(counter.status, ContentStatus.ACTIVE)

    def test_deleted_rows_are_hidden_by_default_manager(self):
        self.thesis_a.soft_delete(actor=self.user)
        self.assertFalse(Thesis.objects.filter(pk=self.thesis_a.pk).exists())
        self.assertTrue(Thesis.all_objects.filter(pk=self.thesis_a.pk).exists())

    def test_nested_counter_must_share_parent_argument_and_thesis(self):
        argument = self.make_argument(self.thesis_a, order=1, body="arg a")
        root = self.make_counter(self.thesis_a, argument, body="root counter", author=self.user)
        nested = self.make_counter(
            self.thesis_a,
            argument,
            body="nested counter",
            author=self.user,
            parent=root,
        )
        self.assertEqual(nested.parent_counter_id, root.id)
        self.assertEqual(nested.target_argument_id, root.target_argument_id)

    def test_nested_counter_rejects_mismatched_parent_argument(self):
        argument_a = self.make_argument(self.thesis_a, order=1, body="arg a")
        argument_a2 = self.make_argument(self.thesis_a, order=2, body="arg a2")
        root = self.make_counter(self.thesis_a, argument_a, body="root counter", author=self.user)
        invalid = Counter(
            thesis=self.thesis_a,
            target_argument=argument_a2,
            parent_counter=root,
            body="invalid nested",
            author=self.user,
        )
        with self.assertRaises(ValidationError):
            invalid.save()

    def test_counter_parent_chain_rejects_cycles(self):
        argument = self.make_argument(self.thesis_a, order=1, body="arg a")
        root = self.make_counter(self.thesis_a, argument, body="root counter", author=self.user)
        child = self.make_counter(
            self.thesis_a,
            argument,
            body="child counter",
            author=self.user,
            parent=root,
        )
        root.parent_counter = child
        with self.assertRaises(ValidationError):
            root.save()


class RebuttalRenderingTests(TestDataMixin, TestCase):
    def setUp(self):
        self.user = self.make_user(prefix="rebuttaluser", username="rebuttaluser")
        self.thesis = self.make_thesis(title="Recursive thesis", author=self.user)
        self.argument = self.make_argument(self.thesis, order=1, body="base argument")
        self.root_counter = self.make_counter(
            self.thesis,
            self.argument,
            body="root counter",
            author=self.user,
        )
        self.child_counter = self.make_counter(
            self.thesis,
            self.argument,
            body="child rebuttal",
            author=self.user,
            parent=self.root_counter,
        )

    def test_thesis_detail_renders_nested_rebuttal(self):
        response = self.client.get(reverse("thinking:thesis_detail", kwargs={"pk": self.thesis.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "root counter")
        self.assertContains(response, "child rebuttal")


class ClaimGraphTests(TestDataMixin, TestCase):
    def setUp(self):
        self.user = self.make_user(prefix="claimuser", username="claimuser")
        self.thesis = self.make_thesis(title="Claim thesis", author=self.user)
        self.support_type = ClaimRelationType.objects.get(code=ClaimRelationType.SUPPORT)
        self.oppose_type = ClaimRelationType.objects.get(code=ClaimRelationType.OPPOSE)

    def test_claim_relation_rejects_cross_thesis_link(self):
        other_thesis = self.make_thesis(
            title="Other thesis",
            author=self.user,
            stance=Thesis.Stance.CON,
        )
        source = self.make_claim(self.thesis, author=self.user, body="Source claim")
        target = self.make_claim(other_thesis, author=self.user, body="Target claim")
        relation = ClaimRelation(
            source_claim=source,
            target_claim=target,
            relation_type=self.support_type,
        )
        with self.assertRaises(ValidationError):
            relation.save()

    def test_claim_relation_rejects_graph_cycle(self):
        root = self.make_claim(self.thesis, author=self.user, body="Root claim")
        child = self.make_claim(self.thesis, author=self.user, body="Child claim")
        ClaimRelation.objects.create(
            source_claim=root,
            target_claim=child,
            relation_type=self.support_type,
        )
        invalid = ClaimRelation(
            source_claim=child,
            target_claim=root,
            relation_type=self.oppose_type,
        )
        with self.assertRaises(ValidationError):
            invalid.save()

    def test_claim_create_view_creates_claim_and_relation(self):
        root = self.make_claim(self.thesis, author=self.user, body="Archive root")
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("thinking:claim_create", kwargs={"pk": self.thesis.pk}),
            {
                "body": "Opposing archive claim",
                "status": ContentStatus.ACTIVE,
                "target_claim": root.pk,
                "relation_type": self.oppose_type.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        created = Claim.objects.get(body="Opposing archive claim")
        self.assertEqual(created.thesis_id, self.thesis.id)
        self.assertTrue(
            ClaimRelation.objects.filter(
                source_claim=root,
                target_claim=created,
                relation_type=self.oppose_type,
            ).exists()
        )

    def test_thesis_detail_renders_claim_archive_and_legacy_mapping(self):
        root = self.make_claim(self.thesis, author=self.user, body="Root archive claim")
        child = self.make_claim(
            self.thesis,
            author=self.user,
            body="Supporting archive claim",
        )
        ClaimRelation.objects.create(
            source_claim=root,
            target_claim=child,
            relation_type=self.support_type,
        )
        argument = self.make_argument(self.thesis, order=1, body="Legacy argument")
        self.make_counter(self.thesis, argument, body="Legacy counter", author=self.user)
        response = self.client.get(reverse("thinking:thesis_detail", kwargs={"pk": self.thesis.pk}))
        self.assertEqual(response.status_code, 200)
        for expected in (
            "Claim Archive",
            "Root archive claim",
            "Supporting archive claim",
            "Legacy debate mapping",
            "Legacy argument",
            "Legacy counter",
        ):
            self.assertContains(response, expected)


class ClaimArchiveExtensionTests(TestDataMixin, TestCase):
    def setUp(self):
        self.user = self.make_user(prefix="archiveuser", username="archiveuser")
        self.thesis = self.make_thesis(title="Archive thesis", author=self.user)
        self.claim = self.make_claim(self.thesis, author=self.user, body="Archive claim body")

    def test_claim_evidence_create_view_persists_evidence(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("thinking:claim_evidence_create", kwargs={"pk": self.claim.pk}),
            {
                "url": "https://example.com/evidence",
                "title": "Evidence title",
                "excerpt": "Evidence excerpt",
            },
        )
        self.assertEqual(response.status_code, 302)
        evidence = ClaimEvidence.objects.get(claim=self.claim)
        self.assertEqual(evidence.created_by_id, self.user.id)
        self.assertEqual(evidence.title, "Evidence title")

    def test_claim_vote_service_blocks_second_vote(self):
        ClaimVote.objects.create(
            claim=self.claim,
            user=self.user,
            vote_type=ClaimVote.VoteType.UPVOTE,
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("thinking:claim_vote", kwargs={"pk": self.claim.pk}),
            {"vote_type": ClaimVote.VoteType.DOWNVOTE},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ClaimVote.objects.filter(claim=self.claim, user=self.user).count(), 1)
        self.assertEqual(
            ClaimVote.objects.get(claim=self.claim, user=self.user).vote_type,
            ClaimVote.VoteType.UPVOTE,
        )

    def test_claim_edit_creates_revision(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("thinking:claim_edit", kwargs={"pk": self.claim.pk}),
            {
                "body": "Updated claim body",
                "status": ContentStatus.ACTIVE,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.body, "Updated claim body")
        revision = ClaimRevision.objects.get(claim=self.claim)
        self.assertEqual(revision.previous_body, "Archive claim body")
        self.assertEqual(revision.edited_by_id, self.user.id)

    def test_thesis_detail_displays_evidence_votes_and_revisions(self):
        ClaimEvidence.objects.create(
            claim=self.claim,
            url="https://example.com/source",
            title="Source title",
            excerpt="Source excerpt",
            created_by=self.user,
        )
        ClaimVote.objects.create(
            claim=self.claim,
            user=self.user,
            vote_type=ClaimVote.VoteType.UPVOTE,
        )
        ClaimRevision.objects.create(
            claim=self.claim,
            previous_body="Original archived body",
            edited_by=self.user,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("thinking:thesis_detail", kwargs={"pk": self.thesis.pk}))
        self.assertEqual(response.status_code, 200)
        for expected in (
            "Evidence",
            "Source title",
            "Score 1",
            "Revision History",
            "Original archived body",
        ):
            self.assertContains(response, expected)


class DebateClaimMappingTests(TestDataMixin, TestCase):
    def setUp(self):
        self.user = self.make_user(prefix="bridgeuser", username="bridgeuser")
        self.thesis = self.make_thesis(title="Bridge thesis", author=self.user)
        self.argument = self.make_argument(self.thesis, order=1, body="Argument to archive")
        self.counter = self.make_counter(
            self.thesis,
            self.argument,
            body="Counter to archive",
            author=self.user,
        )

    def test_argument_claim_conversion_creates_mapping(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("thinking:argument_claim_convert", kwargs={"pk": self.argument.pk}))
        self.assertEqual(response.status_code, 302)
        mapping = DebateClaimMapping.objects.get(argument=self.argument)
        self.assertEqual(mapping.thesis_id, self.thesis.id)
        self.assertEqual(mapping.claim.body, self.argument.body)

    def test_counter_claim_conversion_creates_mapping(self):
        DebateClaimMapping.objects.create(
            thesis=self.thesis,
            argument=self.argument,
            claim=self.make_claim(self.thesis, author=self.user, body="Mapped argument claim"),
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse("thinking:counter_claim_convert", kwargs={"pk": self.counter.pk}))
        self.assertEqual(response.status_code, 302)
        mapping = DebateClaimMapping.objects.get(counter=self.counter)
        self.assertEqual(mapping.claim.body, self.counter.body)
        self.assertTrue(
            ClaimRelation.objects.filter(
                source_claim__debate_mappings__argument=self.argument,
                target_claim=mapping.claim,
                relation_type__code=ClaimRelationType.OPPOSE,
            ).exists()
        )

    def test_duplicate_mapping_prevention(self):
        claim = self.make_claim(self.thesis, author=self.user, body="Existing archived claim")
        DebateClaimMapping.objects.create(thesis=self.thesis, argument=self.argument, claim=claim)
        self.client.force_login(self.user)
        response = self.client.post(reverse("thinking:argument_claim_convert", kwargs={"pk": self.argument.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DebateClaimMapping.objects.filter(argument=self.argument).count(), 1)


class ClaimMergeTests(TestDataMixin, TestCase):
    def setUp(self):
        self.admin_user = self.make_user(prefix="mergeadmin", username="mergeadmin", superuser=True)
        self.other_user = self.make_user(prefix="mergeuser", username="mergeuser")
        self.thesis = self.make_thesis(title="Merge Thesis", author=self.admin_user)
        self.support_type = ClaimRelationType.objects.get(code=ClaimRelationType.SUPPORT)
        self.oppose_type = ClaimRelationType.objects.get(code=ClaimRelationType.OPPOSE)
        self.target_claim = self.make_claim(self.thesis, author=self.admin_user, body="Canonical claim")
        self.source_claim = self.make_claim(self.thesis, author=self.other_user, body="Duplicate claim")
        self.upstream_claim = self.make_claim(self.thesis, author=self.other_user, body="Upstream claim")
        self.downstream_claim = self.make_claim(self.thesis, author=self.other_user, body="Downstream claim")
        ClaimRelation.objects.create(
            source_claim=self.upstream_claim,
            target_claim=self.source_claim,
            relation_type=self.support_type,
        )
        ClaimRelation.objects.create(
            source_claim=self.source_claim,
            target_claim=self.downstream_claim,
            relation_type=self.oppose_type,
        )
        ClaimEvidence.objects.create(
            claim=self.source_claim,
            url="https://example.com/merge",
            title="Merge evidence",
            excerpt="excerpt",
            created_by=self.admin_user,
        )
        ClaimRevision.objects.create(
            claim=self.source_claim,
            previous_body="Original duplicate claim",
            edited_by=self.admin_user,
        )
        ClaimVote.objects.create(
            claim=self.source_claim,
            user=self.other_user,
            vote_type=ClaimVote.VoteType.UPVOTE,
        )
        ClaimVote.objects.create(
            claim=self.target_claim,
            user=self.admin_user,
            vote_type=ClaimVote.VoteType.UPVOTE,
        )
        self.argument = self.make_argument(self.thesis, order=1, body="Mapped argument")
        DebateClaimMapping.objects.create(
            thesis=self.thesis,
            argument=self.argument,
            claim=self.source_claim,
        )

    def test_merge_claims_moves_archive_records_and_mappings(self):
        merge_claims(
            source_claim=self.source_claim,
            target_claim=self.target_claim,
            admin_user=self.admin_user,
            reason="duplicate",
        )
        self.source_claim.refresh_from_db()
        self.assertEqual(self.source_claim.status, ContentStatus.ARCHIVED)
        self.assertEqual(ClaimEvidence.objects.filter(claim=self.target_claim).count(), 1)
        self.assertEqual(ClaimRevision.objects.filter(claim=self.target_claim).count(), 1)
        self.assertEqual(
            DebateClaimMapping.objects.get(argument=self.argument).claim_id,
            self.target_claim.id,
        )
        self.assertTrue(
            ClaimCanonical.objects.filter(
                claim=self.source_claim,
                canonical_claim=self.target_claim,
            ).exists()
        )
        self.assertTrue(
            ClaimMergeLog.objects.filter(
                source_claim=self.source_claim,
                target_claim=self.target_claim,
                merged_by=self.admin_user,
            ).exists()
        )

    def test_merge_claims_moves_relations_to_target(self):
        merge_claims(
            source_claim=self.source_claim,
            target_claim=self.target_claim,
            admin_user=self.admin_user,
            reason="relation merge",
        )
        self.assertTrue(
            ClaimRelation.objects.filter(
                source_claim=self.upstream_claim,
                target_claim=self.target_claim,
                relation_type=self.support_type,
            ).exists()
        )
        self.assertTrue(
            ClaimRelation.objects.filter(
                source_claim=self.target_claim,
                target_claim=self.downstream_claim,
                relation_type=self.oppose_type,
            ).exists()
        )
        self.assertFalse(ClaimRelation.objects.filter(source_claim=self.source_claim).exists())

    def test_merge_claims_deduplicates_votes(self):
        ClaimVote.objects.create(
            claim=self.target_claim,
            user=self.other_user,
            vote_type=ClaimVote.VoteType.DOWNVOTE,
        )
        merge_claims(
            source_claim=self.source_claim,
            target_claim=self.target_claim,
            admin_user=self.admin_user,
            reason="vote merge",
        )
        self.assertEqual(
            ClaimVote.objects.filter(claim=self.target_claim, user=self.other_user).count(),
            1,
        )

    def test_staff_can_preview_merge_tool(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("thinking:claim_merge_preview"), {"q": "claim"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Claim Merge Tool")


class ClaimScoreTests(TestDataMixin, TestCase):
    def setUp(self):
        self.user = self.make_user(prefix="scoreuser", username="scoreuser")
        self.other_user = self.make_user(prefix="scoreuser2", username="scoreuser2")
        self.thesis = self.make_thesis(title="Ranking Thesis", author=self.user)
        self.support_type = ClaimRelationType.objects.get(code=ClaimRelationType.SUPPORT)
        self.oppose_type = ClaimRelationType.objects.get(code=ClaimRelationType.OPPOSE)

    def test_bayesian_vote_score_stabilizes_low_volume_votes(self):
        low_volume = self.make_claim(self.thesis, author=self.user, body="Low volume claim")
        high_volume = self.make_claim(self.thesis, author=self.user, body="High volume claim")
        ClaimVote.objects.create(claim=low_volume, user=self.user, vote_type=ClaimVote.VoteType.UPVOTE)
        for idx in range(5):
            voter = self.make_user(prefix="hv", username=f"hv_{idx}")
            ClaimVote.objects.create(
                claim=high_volume,
                user=voter,
                vote_type=ClaimVote.VoteType.UPVOTE,
            )
        low_score = calculate_claim_score(low_volume)
        assert low_score is not None
        high_score = ClaimScore.objects.get(claim=high_volume)
        self.assertGreater(low_score.bayesian_vote_score, 0.5)
        self.assertLess(low_score.bayesian_vote_score, 1.0)
        self.assertGreater(high_score.bayesian_vote_score, low_score.bayesian_vote_score)

    def test_evidence_score_uses_credibility_inputs(self):
        generic_claim = self.make_claim(self.thesis, author=self.user, body="Generic evidence claim")
        credible_claim = self.make_claim(self.thesis, author=self.user, body="Credible evidence claim")
        ClaimEvidence.objects.create(
            claim=generic_claim,
            url="https://example.com/post",
            title="Generic source",
            source_label="community",
            citation_count=0,
            trust_score=0.8,
            excerpt="Generic excerpt",
            created_by=self.user,
        )
        ClaimEvidence.objects.create(
            claim=credible_claim,
            url="https://www.nature.com/article",
            title="Nature paper",
            source_label="peer_reviewed",
            citation_count=40,
            trust_score=3.5,
            excerpt="Paper excerpt",
            created_by=self.user,
        )
        generic_score = calculate_claim_score(generic_claim)
        assert generic_score is not None
        credible_score = ClaimScore.objects.get(claim=credible_claim)
        self.assertGreater(generic_score.evidence_score, 0.0)
        self.assertGreater(credible_score.evidence_score, generic_score.evidence_score)

    def test_pagerank_propagation_accounts_for_support_and_opposition(self):
        root = self.make_claim(self.thesis, author=self.user, body="Root claim")
        support_child = self.make_claim(self.thesis, author=self.user, body="Support child")
        oppose_child = self.make_claim(self.thesis, author=self.user, body="Oppose child")
        ClaimRelation.objects.create(
            source_claim=root,
            target_claim=support_child,
            relation_type=self.support_type,
        )
        ClaimRelation.objects.create(
            source_claim=root,
            target_claim=oppose_child,
            relation_type=self.oppose_type,
        )
        ClaimVote.objects.create(claim=root, user=self.user, vote_type=ClaimVote.VoteType.UPVOTE)
        calculate_claim_score(root)
        support_child_score = ClaimScore.objects.get(claim=support_child)
        oppose_child_score = ClaimScore.objects.get(claim=oppose_child)
        self.assertGreater(support_child_score.support_score, 0.0)
        self.assertGreater(oppose_child_score.oppose_score, 0.0)
        self.assertGreater(support_child_score.pagerank_score, oppose_child_score.pagerank_score)

    def test_thesis_detail_displays_claim_strength_metrics(self):
        claim = self.make_claim(self.thesis, author=self.user, body="Displayed ranked claim")
        ClaimVote.objects.create(claim=claim, user=self.user, vote_type=ClaimVote.VoteType.UPVOTE)
        response = self.client.get(reverse("thinking:thesis_detail", kwargs={"pk": self.thesis.pk}))
        self.assertEqual(response.status_code, 200)
        for expected in ("Top Ranked Claims", "Strength", "Vote confidence", "PageRank"):
            self.assertContains(response, expected)

    def test_merge_rebuilds_claim_scores_for_target_and_source(self):
        target_claim = self.make_claim(self.thesis, author=self.user, body="Canonical score target")
        source_claim = self.make_claim(self.thesis, author=self.other_user, body="Canonical score source")
        ClaimVote.objects.create(claim=source_claim, user=self.user, vote_type=ClaimVote.VoteType.UPVOTE)
        calculate_claim_score(source_claim)
        self.assertTrue(ClaimScore.objects.filter(claim=source_claim).exists())
        merge_claims(
            source_claim=source_claim,
            target_claim=target_claim,
            admin_user=self.user,
            reason="score merge",
        )
        self.assertFalse(ClaimScore.objects.filter(claim=source_claim).exists())
        target_score = ClaimScore.objects.get(claim=target_claim)
        self.assertGreater(target_score.bayesian_vote_score, 0.5)


class SemanticDuplicateDetectionTests(TestDataMixin, TestCase):
    def setUp(self):
        self.admin_user = self.make_user(prefix="dupadmin", username="dupadmin", superuser=True)
        self.user = self.make_user(prefix="dupuser", username="dupuser")
        self.thesis = self.make_thesis(title="Semantic Duplicate Thesis", author=self.admin_user)

    def test_embedding_generation_creates_normalized_vector(self):
        claim = self.make_claim(self.thesis, author=self.user, body="Smoking causes cancer")
        embedding = generate_claim_embedding(claim=claim)
        self.assertEqual(embedding.embedding_model, "local-hash-embedding-v1")
        self.assertEqual(len(embedding.embedding_vector), 64)
        self.assertTrue(any(value != 0.0 for value in embedding.embedding_vector))

    def test_similarity_detection_finds_semantic_duplicate(self):
        claim_a = self.make_claim(self.thesis, author=self.user, body="Smoking causes cancer")
        claim_b = self.make_claim(
            self.thesis,
            author=self.user,
            body="Cigarette smoking increases the risk of cancer",
        )
        similarity = ClaimSimilarity.objects.get(
            claim_a=min(claim_a, claim_b, key=lambda claim: claim.id),
            claim_b=max(claim_a, claim_b, key=lambda claim: claim.id),
        )
        self.assertGreaterEqual(similarity.similarity_score, 0.82)

    def test_cosine_similarity_scores_equivalent_vectors_highly(self):
        claim_a = self.make_claim(self.thesis, author=self.user, body="Smoking causes cancer")
        claim_b = self.make_claim(self.thesis, author=self.user, body="Smoking causes cancer")
        embedding_a = generate_claim_embedding(claim=claim_a)
        embedding_b = generate_claim_embedding(claim=claim_b)
        similarity_score = compute_cosine_similarity(
            vector_a=embedding_a.embedding_vector,
            vector_b=embedding_b.embedding_vector,
        )
        self.assertGreaterEqual(similarity_score, 0.99)

    def test_duplicate_review_merge_uses_existing_merge_pipeline(self):
        canonical = self.make_claim(self.thesis, author=self.admin_user, body="Smoking causes cancer")
        duplicate = self.make_claim(
            self.thesis,
            author=self.user,
            body="Cigarette smoking increases the risk of cancer",
        )
        argument = self.make_argument(self.thesis, order=1, body="Conversation bridge argument")
        DebateClaimMapping.objects.create(thesis=self.thesis, argument=argument, claim=duplicate)
        review_duplicate_pair(
            claim_a=canonical,
            claim_b=duplicate,
            decision=ClaimDuplicateReview.Decision.MERGE,
            reviewed_by=self.admin_user,
            reason="semantic duplicate",
            merge_func=merge_claims,
        )
        self.assertTrue(
            ClaimCanonical.objects.filter(
                claim=duplicate,
                canonical_claim=canonical,
            ).exists()
        )
        self.assertEqual(DebateClaimMapping.objects.get(argument=argument).claim_id, canonical.id)

    def test_staff_duplicate_review_view_executes_ignore_workflow(self):
        claim_a = self.make_claim(self.thesis, author=self.admin_user, body="Smoking causes cancer")
        claim_b = self.make_claim(
            self.thesis,
            author=self.user,
            body="Cigarette smoking increases the risk of cancer",
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("thinking:claim_duplicate_review"),
            {
                "claim_a": min(claim_a.id, claim_b.id),
                "claim_b": max(claim_a.id, claim_b.id),
                "decision": ClaimDuplicateReview.Decision.IGNORE,
                "reason": "not actionable",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ClaimDuplicateReview.objects.filter(decision=ClaimDuplicateReview.Decision.IGNORE).exists()
        )


class ClaimNormalizationTests(TestDataMixin, TestCase):
    def setUp(self):
        self.user = self.make_user(prefix="normuser", username="normuser")
        self.thesis = self.make_thesis(title="Normalization Thesis", author=self.user)

    def test_parse_claim_text_to_triple_extracts_subject_predicate_object(self):
        parsed = parse_claim_text_to_triple(claim_text="Smoking causes cancer")
        assert parsed is not None
        self.assertEqual(parsed["subject"], "Smoking")
        self.assertEqual(parsed["predicate"], "cause")
        self.assertEqual(parsed["object"], "cancer")

    def test_canonicalize_entity_name_normalizes_articles_and_case(self):
        self.assertEqual(canonicalize_entity_name("The Smoking"), "smoking")
        self.assertEqual(canonicalize_entity_name("Cigarette Smoking"), "smoking")

    def test_normalize_claim_creates_triple_and_entities(self):
        claim = self.make_claim(self.thesis, author=self.user, body="Smoking causes cancer")
        normalized = normalize_claim(claim=claim)
        self.assertIsNotNone(normalized)
        self.assertTrue(ClaimNormalized.objects.filter(claim=claim).exists())
        triple = ClaimTriple.objects.get(claim=claim)
        self.assertEqual(triple.subject_entity.canonical_name, "smoking")
        self.assertEqual(triple.predicate.name, "cause")
        self.assertEqual(triple.object_entity.canonical_name, "cancer")
        self.assertTrue(ClaimEntity.objects.filter(canonical_name="smoking").exists())
        self.assertTrue(ClaimPredicate.objects.filter(name="cause").exists())

    def test_claim_aliases_are_created_for_surface_forms(self):
        claim = self.make_claim(
            self.thesis,
            author=self.user,
            body="Cigarette smoking increases the risk of cancer",
        )
        normalize_claim(claim=claim)
        aliases = list(ClaimAlias.objects.filter(claim=claim).values_list("alias_text", flat=True))
        self.assertIn("Cigarette smoking", aliases)
        self.assertIn("cancer", aliases)


class ThinkingTemplateIntegrationTests(TestDataMixin, TestCase):
    def setUp(self):
        self.user = self.make_user(prefix="author2", username="author2")
        for idx in range(25):
            self.make_thesis(
                title=f"Thesis {idx}",
                author=self.user,
                stance=Thesis.Stance.SUSPEND,
            )

    def test_pagination_preserves_sort_parameter(self):
        response = self.client.get(reverse("thinking:thesis_list"), {"sort": "new", "page": 2})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "sort=new&page=1")

    def test_base_template_has_favicon_fallback(self):
        response = self.client.get(reverse("thinking:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/favicon.ico"')


class CounterCreateLabelTests(TestDataMixin, TestCase):
    def setUp(self):
        self.user = self.make_user(prefix="counterlabeluser", username="counterlabeluser")
        self.thesis = self.make_thesis(title="Counter Label Thesis", author=self.user)
        self.arguments = [
            self.make_argument(
                self.thesis,
                order=idx,
                body=f"Argument body {idx} for dropdown label verification.",
            )
            for idx in range(1, 4)
        ]

    def test_counter_create_dropdown_uses_a_order_labels(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("thinking:counter_create", kwargs={"pk": self.thesis.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Target argument")
        for idx in range(1, 4):
            self.assertContains(response, f"A{idx}")
            self.assertNotContains(response, f"{self.thesis.pk}:{idx}")

    def test_counter_create_post_creates_counter_and_redirects(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("thinking:counter_create", kwargs={"pk": self.thesis.pk}),
            {
                "target_argument": self.arguments[1].pk,
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
            target_argument=self.arguments[1],
            author=self.user,
        )
        self.assertEqual(created.body, "Counter body from test")


class ArgumentAdminUxTests(TestDataMixin, TestCase):
    def setUp(self):
        self.superuser = self.make_user(prefix="argadmin", username="argadmin", superuser=True)
        self.thesis = self.make_thesis(title="Admin Context Thesis", author=self.superuser)
        self.argument = self.make_argument(
            self.thesis,
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

        target_argument_field = cast(
            ForeignKey,
            Counter._meta.get_field("target_argument"),
        )

        form_field = counter_admin.formfield_for_foreignkey(
            target_argument_field,
            request,
        )

        assert form_field is not None

        typed_form_field = cast(ModelChoiceField, form_field)

        choices = cast(list[tuple[Any, str]], typed_form_field.choices)

        labels = [label for _, label in choices]

        self.assertTrue(any("Admin Context Thesis" in label for label in labels))
        self.assertTrue(any("A1" in label for label in labels))


class AuthBoundaryTests(TestDataMixin, TestCase):
    def setUp(self):
        cache.clear()
        self.user = self.make_user(prefix="normaluser", username="normaluser")
        self.staff = self.make_user(
            prefix="staffuser",
            username="staffuser",
            is_staff=True,
        )
        self.moderator = self.make_user(prefix="moderatoruser", username="moderatoruser")
        self.operator = self.make_user(prefix="operatoruser", username="operatoruser")
        UserRole.objects.create(user=self.moderator, role=SiteRole.MODERATOR)
        UserRole.objects.create(user=self.operator, role=SiteRole.OPERATOR)
        UserRole.objects.create(user=self.staff, role=SiteRole.USER)

    def assert_moderation_context_titles(self, response, *, includes=(), excludes=()):
        ctx = self.get_context(response)
        titles = {
            row["target_label"]
            for row in ctx["open_reports"]
            if row["report"].target_type == ContentReport.TargetType.THESIS
        }
        for title in includes:
            self.assertIn(title, titles)
        for title in excludes:
            self.assertNotIn(title, titles)

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
        thesis = self.make_thesis(title="Aggregate Thesis", author=self.user)
        self.make_reports_for_thesis(thesis, 3, prefix="mod_agg")
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "open 3")
        self.assertContains(response, "Auto-threshold")

    def test_moderation_panel_filters_target_type_reason_and_only_auto(self):
        thesis = self.make_thesis(title="Filter Thesis", author=self.user)
        argument = self.make_argument(thesis, order=1, body="arg")
        counter = self.make_counter(thesis, argument, body="counter", author=self.user)
        self.make_reports_for_thesis(thesis, 3, prefix="mod_filter_t")
        counter_reporter = self.make_user(prefix="mod_filter_counter", username="mod_filter_counter")
        self.make_report(reporter=counter_reporter, counter=counter, reason="other")
        self.client.force_login(self.moderator)
        target_type_response = self.client.get(reverse("thinking:moderation_panel"), {"target_type": "counter"})
        self.assertEqual(target_type_response.status_code, 200)
        self.assertContains(target_type_response, f"Counter #{counter.pk}")
        self.assertNotContains(target_type_response, thesis.title)
        reason_response = self.client.get(reverse("thinking:moderation_panel"), {"reason": "other"})
        self.assertEqual(reason_response.status_code, 200)
        self.assertContains(reason_response, f"Counter #{counter.pk}")
        self.assertNotContains(reason_response, thesis.title)
        only_auto_response = self.client.get(reverse("thinking:moderation_panel"), {"only_auto": "1"})
        self.assertEqual(only_auto_response.status_code, 200)
        self.assertContains(only_auto_response, thesis.title)
        self.assertNotContains(only_auto_response, f"Counter #{counter.pk}")

    def test_moderation_panel_marks_deleted_targets(self):
        thesis = self.make_thesis(title="Deleted Marker Thesis", author=self.user)
        thesis.soft_delete(actor=self.moderator)
        self.make_report(reporter=self.user, thesis=thesis, reason="spam")
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deleted")

    def test_moderation_panel_query_count_is_bounded(self):
        thesis = self.make_thesis(title="Query Bound Thesis", author=self.user)
        self.make_reports_for_thesis(thesis, 6, prefix="mod_q")
        self.client.force_login(self.moderator)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 12)

    def test_metrics_tab_access_boundaries(self):
        anon_response = self.client.get(reverse("thinking:moderation_panel"), {"tab": "metrics", "since_days": 30})
        self.assertEqual(anon_response.status_code, 302)
        self.assertIn("/auth/?next=", anon_response["Location"])
        self.client.force_login(self.user)
        user_response = self.client.get(reverse("thinking:moderation_panel"), {"tab": "metrics", "since_days": 30})
        self.assertEqual(user_response.status_code, 403)
        self.client.force_login(self.moderator)
        mod_response = self.client.get(reverse("thinking:moderation_panel"), {"tab": "metrics", "since_days": 30})
        self.assertEqual(mod_response.status_code, 200)
        self.assertContains(mod_response, "Moderator Decision Metrics")

    def test_metrics_tab_shows_status_counts_latency_operator_and_hot_targets(self):
        now = timezone.now()
        thesis = self.make_thesis(title="Metrics Thesis", author=self.user)
        old_reporter = self.make_user(prefix="metrics_old", username="metrics_old")
        old_report = self.make_report(
            reporter=old_reporter,
            thesis=thesis,
            reason="other",
            status=ContentReport.Status.OPEN,
        )
        self.set_report_time(old_report, created_at=now - timedelta(days=45))
        open_reports = self.make_reports_for_thesis(thesis, 3, prefix="metrics_open")
        for report in open_reports:
            self.set_report_time(report, created_at=now - timedelta(days=1))
        resolved_reporters = [
            self.make_user(prefix="metrics_resolved", username=f"metrics_resolved_{idx}")
            for idx in range(3)
        ]
        resolved_report_a = self.make_report(
            reporter=resolved_reporters[0],
            thesis=thesis,
            reason="hate",
            status=ContentReport.Status.RESOLVED,
            resolved_by=self.moderator,
            resolved_at=now - timedelta(days=1),
        )
        resolved_report_b = self.make_report(
            reporter=resolved_reporters[1],
            thesis=thesis,
            reason="harassment",
            status=ContentReport.Status.RESOLVED,
            resolved_by=self.operator,
            resolved_at=now - timedelta(days=1),
        )
        dismissed_report = self.make_report(
            reporter=resolved_reporters[2],
            thesis=thesis,
            reason="other",
            status=ContentReport.Status.DISMISSED,
            resolved_by=self.moderator,
            resolved_at=now - timedelta(days=1),
        )
        self.set_report_time(
            resolved_report_a,
            created_at=now - timedelta(days=1, minutes=10),
            resolved_at=now - timedelta(days=1),
        )
        self.set_report_time(
            resolved_report_b,
            created_at=now - timedelta(days=1, minutes=20),
            resolved_at=now - timedelta(days=1),
        )
        self.set_report_time(
            dismissed_report,
            created_at=now - timedelta(days=1, hours=1),
            resolved_at=now - timedelta(days=1),
        )
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"), {"tab": "metrics", "since_days": 30})
        self.assertEqual(response.status_code, 200)
        metrics = self.get_context(response)["metrics"]
        self.assertEqual(metrics["counts_by_status"]["open_count"], 3)
        self.assertEqual(metrics["counts_by_status"]["resolved_count"], 2)
        self.assertEqual(metrics["counts_by_status"]["dismissed_count"], 1)
        self.assertEqual(metrics["latency"]["median_seconds"], 1200)
        self.assertEqual(metrics["latency"]["p90_seconds"], 3600)
        operator_counts = {row["operator_username"]: row["decision_count"] for row in metrics["operator_decisions"]}
        self.assertEqual(operator_counts[self.moderator.username], 2)
        self.assertEqual(operator_counts[self.operator.username], 1)
        operator_workload = {row["operator_username"]: row for row in metrics["operator_metrics"]}
        self.assertEqual(operator_workload[self.moderator.username]["decisions_count"], 2)
        self.assertEqual(operator_workload[self.moderator.username]["resolved_count"], 1)
        self.assertEqual(operator_workload[self.moderator.username]["dismissed_count"], 1)
        self.assertEqual(operator_workload[self.moderator.username]["median_seconds"], 600)
        self.assertEqual(operator_workload[self.moderator.username]["p90_seconds"], 3600)
        self.assertEqual(operator_workload[self.operator.username]["decisions_count"], 1)
        self.assertEqual(operator_workload[self.operator.username]["median_seconds"], 1200)
        self.assertFalse(metrics["operator_not_found"])
        self.assertEqual(metrics["hot_targets_count"], 1)
        self.assertEqual(metrics["hot_reports_total"], 3)
        for expected in (
            "(1200s)",
            "(3600s)",
            self.moderator.username,
            self.operator.username,
            "Hot targets (open_count",
            "Open reports on hot targets: 3",
            "Operator Workload (window: 30 days)",
        ):
            self.assertContains(response, expected)

    def test_metrics_tab_operator_filter_scopes_operator_workload(self):
        now = timezone.now().replace(microsecond=0)
        thesis = self.make_thesis(title="Metrics Operator Filter Thesis", author=self.user)
        report_one = self.make_report(
            reporter=self.user,
            thesis=thesis,
            reason="spam",
            status=ContentReport.Status.RESOLVED,
            resolved_by=self.moderator,
            resolved_at=now - timedelta(hours=2),
        )
        report_two = self.make_report(
            reporter=self.user,
            thesis=thesis,
            reason="other",
            status=ContentReport.Status.DISMISSED,
            resolved_by=self.operator,
            resolved_at=now - timedelta(hours=1),
        )
        self.set_report_time(
            report_one,
            created_at=now - timedelta(hours=2, minutes=20),
            resolved_at=now - timedelta(hours=2),
        )
        self.set_report_time(
            report_two,
            created_at=now - timedelta(hours=1, minutes=10),
            resolved_at=now - timedelta(hours=1),
        )
        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"tab": "metrics", "since_days": 30, "operator": self.operator.username},
        )
        self.assertEqual(response.status_code, 200)
        metrics = self.get_context(response)["metrics"]
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
        metrics = self.get_context(response)["metrics"]
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
        thesis = self.make_thesis(title="CSV Metrics Thesis", author=self.user)
        resolved_report = self.make_report(
            reporter=self.user,
            thesis=thesis,
            reason="spam",
            status=ContentReport.Status.RESOLVED,
            resolved_by=self.moderator,
            resolved_at=fixed_now - timedelta(hours=1),
        )
        dismissed_report = self.make_report(
            reporter=self.user,
            thesis=thesis,
            reason="other",
            status=ContentReport.Status.DISMISSED,
            resolved_by=self.operator,
            resolved_at=fixed_now - timedelta(hours=1),
        )
        open_report = self.make_report(
            reporter=self.user,
            thesis=thesis,
            reason="harassment",
            status=ContentReport.Status.OPEN,
        )
        self.set_report_time(
            resolved_report,
            created_at=fixed_now - timedelta(hours=1, minutes=20),
            resolved_at=fixed_now - timedelta(hours=1),
        )
        self.set_report_time(
            dismissed_report,
            created_at=fixed_now - timedelta(hours=1, minutes=10),
            resolved_at=fixed_now - timedelta(hours=1),
        )
        self.set_report_time(open_report, created_at=fixed_now - timedelta(hours=2))
        self.client.force_login(self.moderator)
        with patch("thinking.views.timezone.now", return_value=fixed_now):
            response = self.client.get(reverse("thinking:moderation_metrics_csv"), {"since_days": 30})
        self.assertEqual(response.status_code, 200)
        content_disposition = response["Content-Disposition"]
        self.assertIn("attachment", content_disposition)
        self.assertIn("moderation_metrics_30d_", content_disposition)
        csv_body = response.content.decode("utf-8")
        for expected in (
            "Status counts",
            "open_count,1",
            "resolved_count,1",
            "dismissed_count,1",
            "latency_median_seconds",
            "latency_p90_seconds",
            self.moderator.username,
            self.operator.username,
            "Operator workload",
        ):
            self.assertIn(expected, csv_body)

    def test_metrics_csv_export_operator_filter(self):
        fixed_now = timezone.now().replace(microsecond=0)
        thesis = self.make_thesis(title="CSV Metrics Filter Thesis", author=self.user)
        one = self.make_report(
            reporter=self.user,
            thesis=thesis,
            reason="spam",
            status=ContentReport.Status.RESOLVED,
            resolved_by=self.moderator,
            resolved_at=fixed_now - timedelta(minutes=30),
        )
        two = self.make_report(
            reporter=self.user,
            thesis=thesis,
            reason="other",
            status=ContentReport.Status.DISMISSED,
            resolved_by=self.operator,
            resolved_at=fixed_now - timedelta(minutes=20),
        )
        self.set_report_time(
            one,
            created_at=fixed_now - timedelta(minutes=50),
            resolved_at=fixed_now - timedelta(minutes=30),
        )
        self.set_report_time(
            two,
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
        thesis = self.make_thesis(title="Stale Metrics Thesis", author=self.user)
        stale_reporter = self.make_user(prefix="stale_metrics_old", username="stale_metrics_old")
        fresh_reporter = self.make_user(prefix="stale_metrics_new", username="stale_metrics_new")
        stale_report = self.make_report(reporter=stale_reporter, thesis=thesis, reason="spam")
        fresh_report = self.make_report(reporter=fresh_reporter, thesis=thesis, reason="other")
        self.set_report_time(stale_report, created_at=now - timedelta(hours=72))
        self.set_report_time(fresh_report, created_at=now - timedelta(hours=6))
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"), {"tab": "metrics", "since_days": 30})
        self.assertEqual(response.status_code, 200)
        metrics = self.get_context(response)["metrics"]
        self.assertEqual(metrics["stale_threshold_hours"], 48)
        self.assertEqual(metrics["stale_open_count"], 1)
        self.assertIsNotNone(metrics["oldest_stale_open_age_seconds"])
        self.assertGreaterEqual(metrics["oldest_stale_open_age_seconds"], 71 * 3600)
        self.assertLessEqual(metrics["oldest_stale_open_age_seconds"], 73 * 3600)

    def test_metrics_tab_includes_escalation_distribution_and_max_level(self):
        level_0_thesis = self.make_thesis(title="Escalation L0 Thesis", author=self.user)
        level_1_thesis = self.make_thesis(title="Escalation L1 Thesis", author=self.user)
        level_2_thesis = self.make_thesis(title="Escalation L2 Thesis", author=self.user)
        level_3_thesis = self.make_thesis(title="Escalation L3 Thesis", author=self.user)
        for thesis, report_count, prefix in (
            (level_0_thesis, 2, "escalation_l0"),
            (level_1_thesis, ESCALATION_LEVEL_1, "escalation_l1"),
            (level_2_thesis, ESCALATION_LEVEL_2, "escalation_l2"),
            (level_3_thesis, ESCALATION_LEVEL_3, "escalation_l3"),
        ):
            self.make_reports_for_thesis(thesis, report_count, prefix=prefix)
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"), {"tab": "metrics", "since_days": 30})
        self.assertEqual(response.status_code, 200)
        metrics = self.get_context(response)["metrics"]
        self.assertEqual(metrics["escalation_distribution"]["level_1_targets"], 3)
        self.assertEqual(metrics["escalation_distribution"]["level_2_targets"], 2)
        self.assertEqual(metrics["escalation_distribution"]["level_3_targets"], 1)
        self.assertEqual(metrics["max_escalation_level"], 3)
        for expected in (
            "Level 1 targets: 3",
            "Level 2 targets: 2",
            "Level 3 targets: 1",
            "Highest escalation level active: 3",
        ):
            self.assertContains(response, expected)

    def test_reports_tab_shows_stale_badge_for_stale_only(self):
        now = timezone.now()
        stale_thesis = self.make_thesis(title="Stale Badge Thesis", author=self.user)
        fresh_thesis = self.make_thesis(
            title="Fresh Badge Thesis",
            author=self.user,
            stance=Thesis.Stance.CON,
        )
        stale_report = self.make_report(reporter=self.user, thesis=stale_thesis, reason="spam")
        fresh_report = self.make_report(reporter=self.user, thesis=fresh_thesis, reason="other")
        self.set_report_time(stale_report, created_at=now - timedelta(hours=60))
        self.set_report_time(fresh_report, created_at=now - timedelta(hours=6))
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "STALE")
        self.assertContains(response, stale_thesis.title)
        self.assertContains(response, fresh_thesis.title)

    def test_reports_tab_shows_escalation_badges_and_boundaries(self):
        theses = {
            "l0": self.make_thesis(title="Esc Reports L0", author=self.user),
            "l1": self.make_thesis(title="Esc Reports L1", author=self.user),
            "l2": self.make_thesis(title="Esc Reports L2", author=self.user),
            "l3": self.make_thesis(title="Esc Reports L3", author=self.user),
        }
        for key, report_count in (
            ("l0", ESCALATION_LEVEL_1 - 1),
            ("l1", ESCALATION_LEVEL_1),
            ("l2", ESCALATION_LEVEL_2),
            ("l3", ESCALATION_LEVEL_3),
        ):
            self.make_reports_for_thesis(theses[key], report_count, prefix=f"esc_reports_{key}")
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 200)
        rows_by_title = {
            row["target_label"]: row
            for row in self.get_context(response)["open_reports"]
            if row["report"].target_type == ContentReport.TargetType.THESIS
        }
        self.assertEqual(rows_by_title["Esc Reports L0"]["escalation_level"], 0)
        self.assertEqual(rows_by_title["Esc Reports L1"]["escalation_level"], 1)
        self.assertEqual(rows_by_title["Esc Reports L2"]["escalation_level"], 2)
        self.assertEqual(rows_by_title["Esc Reports L3"]["escalation_level"], 3)
        for expected in ("ESC L1", "ESC L2", "ESC L3"):
            self.assertContains(response, expected)

    def test_escalation_level_filter_limits_targets_by_minimum_tier(self):
        low_thesis = self.make_thesis(title="Esc Filter Low", author=self.user)
        level_two_thesis = self.make_thesis(title="Esc Filter L2", author=self.user)
        level_three_thesis = self.make_thesis(title="Esc Filter L3", author=self.user)
        for thesis, count, prefix in (
            (low_thesis, ESCALATION_LEVEL_1, "esc_filter_low"),
            (level_two_thesis, ESCALATION_LEVEL_2, "esc_filter_l2"),
            (level_three_thesis, ESCALATION_LEVEL_3, "esc_filter_l3"),
        ):
            self.make_reports_for_thesis(thesis, count, prefix=prefix)
        self.client.force_login(self.moderator)
        response = self.client.get(
            reverse("thinking:moderation_panel"),
            {"target_type": "thesis", "escalation_level": "2"},
        )
        self.assertEqual(response.status_code, 200)
        self.assert_moderation_context_titles(
            response,
            includes=(level_two_thesis.title, level_three_thesis.title),
            excludes=(low_thesis.title,),
        )

    def test_reports_row_can_show_stale_and_escalation_badges_together(self):
        now = timezone.now()
        thesis = self.make_thesis(title="Esc Stale Combined", author=self.user)
        reports = self.make_reports_for_thesis(thesis, ESCALATION_LEVEL_1, prefix="esc_stale")
        self.set_report_time(reports[0], created_at=now - timedelta(hours=60))
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(response.status_code, 200)
        matching_rows = [
            row
            for row in self.get_context(response)["open_reports"]
            if row["target_label"] == thesis.title
        ]
        self.assertTrue(any(row["is_stale"] for row in matching_rows))
        self.assertTrue(any(row["escalation_level"] >= 1 for row in matching_rows))
        self.assertContains(response, "STALE")
        self.assertContains(response, "ESC L1")

    def test_priority_sort_orders_by_escalation_first(self):
        now = timezone.now()
        low_thesis = self.make_thesis(title="Priority Low Esc", author=self.user)
        high_thesis = self.make_thesis(title="Priority High Esc", author=self.user)
        self.make_reports_for_thesis(low_thesis, 3, prefix="priority_low")
        self.make_reports_for_thesis(high_thesis, 10, prefix="priority_high")
        ContentReport.objects.filter(thesis_id=low_thesis.pk).update(created_at=now - timedelta(hours=2))
        ContentReport.objects.filter(thesis_id=high_thesis.pk).update(created_at=now - timedelta(hours=1))
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"), {"sort": "priority"})
        self.assertEqual(response.status_code, 200)
        first_row = self.get_context(response)["open_reports"][0]
        self.assertEqual(first_row["target_label"], high_thesis.title)
        self.assertEqual(first_row["escalation_level"], 3)

    def test_priority_sort_prefers_stale_within_same_escalation(self):
        now = timezone.now()
        stale_thesis = self.make_thesis(title="Priority Stale L2", author=self.user)
        fresh_thesis = self.make_thesis(title="Priority Fresh L2", author=self.user)
        self.make_reports_for_thesis(stale_thesis, 5, prefix="priority_stale")
        self.make_reports_for_thesis(fresh_thesis, 5, prefix="priority_fresh")
        ContentReport.objects.filter(thesis_id=stale_thesis.pk).update(created_at=now - timedelta(hours=60))
        ContentReport.objects.filter(thesis_id=fresh_thesis.pk).update(created_at=now - timedelta(hours=2))
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"), {"sort": "priority"})
        self.assertEqual(response.status_code, 200)
        first_row = self.get_context(response)["open_reports"][0]
        self.assertEqual(first_row["target_label"], stale_thesis.title)
        self.assertTrue(first_row["is_stale"])
        self.assertEqual(first_row["escalation_level"], 2)

    def test_priority_sort_uses_oldest_first_as_tiebreaker(self):
        now = timezone.now()
        older_thesis = self.make_thesis(title="Priority Oldest", author=self.user)
        newer_thesis = self.make_thesis(title="Priority Newer", author=self.user)
        self.make_reports_for_thesis(older_thesis, 5, prefix="priority_old")
        self.make_reports_for_thesis(newer_thesis, 5, prefix="priority_new")
        ContentReport.objects.filter(thesis_id=older_thesis.pk).update(created_at=now - timedelta(hours=70))
        ContentReport.objects.filter(thesis_id=newer_thesis.pk).update(created_at=now - timedelta(hours=55))
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"), {"sort": "priority"})
        self.assertEqual(response.status_code, 200)
        first_row = self.get_context(response)["open_reports"][0]
        self.assertEqual(first_row["target_label"], older_thesis.title)
        self.assertTrue(first_row["is_stale"])
        self.assertEqual(first_row["escalation_level"], 2)

    def test_default_sort_order_is_unchanged_without_priority(self):
        older_thesis = self.make_thesis(title="Default Older", author=self.user)
        newer_thesis = self.make_thesis(title="Default Newer", author=self.user)
        older_report = self.make_report(
            reporter=self.make_user(prefix="default_older_user", username="default_older_user"),
            thesis=older_thesis,
            reason="spam",
        )
        newer_report = self.make_report(
            reporter=self.make_user(prefix="default_newer_user", username="default_newer_user"),
            thesis=newer_thesis,
            reason="spam",
        )
        now = timezone.now()
        self.set_report_time(older_report, created_at=now - timedelta(hours=5))
        self.set_report_time(newer_report, created_at=now - timedelta(hours=1))
        self.client.force_login(self.moderator)
        default_response = self.client.get(reverse("thinking:moderation_panel"))
        self.assertEqual(default_response.status_code, 200)
        first_default = self.get_context(default_response)["open_reports"][0]
        self.assertEqual(first_default["target_label"], newer_thesis.title)

    def test_priority_sort_cap_is_stable_for_large_filtered_set(self):
        thesis = self.make_thesis(title="Priority Cap Thesis", author=self.user)
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
        response = self.client.get(reverse("thinking:moderation_panel"), {"sort": "priority"})
        self.assertEqual(response.status_code, 200)
        context = self.get_context(response)
        self.assertTrue(context["priority_mode"])
        self.assertTrue(context["priority_candidates_capped"])
        self.assertEqual(context["open_reports_count"], MAX_PRIORITY_SORT_CANDIDATES)
        self.assertContains(response, "Priority mode capped to oldest")

    def test_stale_only_filter_limits_to_stale_open(self):
        now = timezone.now()
        stale_thesis = self.make_thesis(title="Stale Filter Thesis", author=self.user)
        fresh_thesis = self.make_thesis(
            title="Fresh Filter Thesis",
            author=self.user,
            stance=Thesis.Stance.CON,
        )
        resolved_thesis = self.make_thesis(
            title="Resolved Old Thesis",
            author=self.user,
            stance=Thesis.Stance.SUSPEND,
        )
        stale_open = self.make_report(reporter=self.user, thesis=stale_thesis, reason="spam")
        fresh_open = self.make_report(reporter=self.user, thesis=fresh_thesis, reason="other")
        resolved_old = self.make_report(
            reporter=self.user,
            thesis=resolved_thesis,
            reason="hate",
            status=ContentReport.Status.RESOLVED,
            resolved_by=self.moderator,
            resolved_at=now - timedelta(hours=1),
        )
        self.set_report_time(stale_open, created_at=now - timedelta(hours=72))
        self.set_report_time(fresh_open, created_at=now - timedelta(hours=4))
        self.set_report_time(
            resolved_old,
            created_at=now - timedelta(hours=72),
            resolved_at=now - timedelta(hours=1),
        )
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:moderation_panel"), {"stale_only": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, stale_thesis.title)
        self.assertNotContains(response, fresh_thesis.title)
        self.assertNotContains(response, resolved_thesis.title)

    def test_stale_boundary_created_at_equals_cutoff_is_not_stale(self):
        fixed_now = timezone.now().replace(microsecond=0)
        thesis = self.make_thesis(title="Boundary Stale Thesis", author=self.user)
        edge_report = self.make_report(reporter=self.user, thesis=thesis, reason="spam")
        self.set_report_time(edge_report, created_at=fixed_now - timedelta(hours=48))
        self.client.force_login(self.moderator)
        with patch("thinking.views.timezone.now", return_value=fixed_now):
            response = self.client.get(reverse("thinking:moderation_panel"), {"stale_only": "1"})
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
                action="moderation.mark_reviewed",
                actor_id=self.moderator.id,
            ).exists()
        )

    def test_user_cannot_change_thesis_status(self):
        thesis = self.make_thesis(title="Status Thesis", author=self.user)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("thinking:moderation_thesis_set_status", kwargs={"pk": thesis.pk}),
            {"status": ContentStatus.ARCHIVED},
        )
        self.assertEqual(response.status_code, 403)
        thesis.refresh_from_db()
        self.assertEqual(thesis.status, ContentStatus.ACTIVE)

    def test_moderator_can_change_thesis_status_and_audit_is_written(self):
        thesis = self.make_thesis(title="Status Thesis 2", author=self.user)
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
        log_row = AuditLog.objects.filter(action="moderation.status_change").latest("id")
        self.assertEqual(log_row.actor_id, self.moderator.id)
        self.assertEqual(log_row.metadata.get("old_status"), ContentStatus.ACTIVE)
        self.assertEqual(log_row.metadata.get("new_status"), ContentStatus.ARCHIVED)

    def test_non_moderator_does_not_see_archived_thesis_in_list(self):
        self.make_thesis(title="Visible Active", author=self.user, status=ContentStatus.ACTIVE)
        self.make_thesis(title="Hidden Archived", author=self.user, status=ContentStatus.ARCHIVED)
        response = self.client.get(reverse("thinking:thesis_list"))
        self.assertContains(response, "Visible Active")
        self.assertNotContains(response, "Hidden Archived")

    def test_moderator_can_include_inactive_in_list(self):
        self.make_thesis(title="Visible Active Mod", author=self.user, status=ContentStatus.ACTIVE)
        self.make_thesis(title="Visible Archived Mod", author=self.user, status=ContentStatus.ARCHIVED)
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:thesis_list"), {"include_inactive": 1})
        self.assertContains(response, "Visible Active Mod")
        self.assertContains(response, "Visible Archived Mod")

    def test_deleted_thesis_hidden_from_normal_user_list_and_detail(self):
        deleted = self.make_thesis(title="Hidden Deleted", author=self.user)
        deleted.soft_delete(actor=self.moderator)
        list_response = self.client.get(reverse("thinking:thesis_list"))
        self.assertNotContains(list_response, "Hidden Deleted")
        detail_response = self.client.get(reverse("thinking:thesis_detail", kwargs={"pk": deleted.pk}))
        self.assertEqual(detail_response.status_code, 404)

    def test_moderator_can_include_deleted_in_list(self):
        visible = self.make_thesis(title="Visible Active Deleted Toggle", author=self.user)
        deleted = self.make_thesis(title="Visible Deleted Mod", author=self.user)
        deleted.soft_delete(actor=self.moderator)
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("thinking:thesis_list"), {"include_deleted": 1})
        self.assertContains(response, visible.title)
        self.assertContains(response, deleted.title)

    def test_user_cannot_soft_delete_or_restore(self):
        thesis = self.make_thesis(title="Delete Blocked", author=self.user)
        self.client.force_login(self.user)
        delete_response = self.client.post(reverse("thinking:moderation_thesis_delete", kwargs={"pk": thesis.pk}))
        self.assertEqual(delete_response.status_code, 403)
        thesis.refresh_from_db()
        self.assertFalse(thesis.is_deleted)
        thesis.soft_delete(actor=self.moderator)
        restore_response = self.client.post(reverse("thinking:moderation_thesis_restore", kwargs={"pk": thesis.pk}))
        self.assertEqual(restore_response.status_code, 403)
        thesis.refresh_from_db()
        self.assertTrue(thesis.is_deleted)

    def test_moderator_can_soft_delete_and_restore_thesis_with_audit(self):
        thesis = self.make_thesis(title="Delete Allowed", author=self.user)
        self.client.force_login(self.moderator)
        delete_response = self.client.post(reverse("thinking:moderation_thesis_delete", kwargs={"pk": thesis.pk}))
        self.assertEqual(delete_response.status_code, 302)
        thesis.refresh_from_db()
        self.assertTrue(thesis.is_deleted)
        self.assertEqual(thesis.deleted_by_id, self.moderator.id)
        delete_log = AuditLog.objects.filter(action="moderation.soft_delete").latest("id")
        self.assertEqual(delete_log.actor_id, self.moderator.id)
        self.assertEqual(delete_log.metadata.get("status"), thesis.status)
        self.assertEqual(delete_log.metadata.get("id"), str(thesis.id))
        restore_response = self.client.post(reverse("thinking:moderation_thesis_restore", kwargs={"pk": thesis.pk}))
        self.assertEqual(restore_response.status_code, 302)
        thesis.refresh_from_db()
        self.assertFalse(thesis.is_deleted)
        self.assertIsNone(thesis.deleted_by_id)
        restore_log = AuditLog.objects.filter(action="moderation.restore").latest("id")
        self.assertEqual(restore_log.actor_id, self.moderator.id)
        self.assertEqual(restore_log.metadata.get("id"), str(thesis.id))

    def test_moderator_can_soft_delete_and_restore_counter_with_audit(self):
        thesis = self.make_thesis(title="Counter Delete Thesis", author=self.user)
        argument = self.make_argument(thesis, order=1, body="arg")
        counter = self.make_counter(thesis, argument, body="counter", author=self.user)
        self.client.force_login(self.moderator)
        delete_response = self.client.post(reverse("thinking:moderation_counter_delete", kwargs={"pk": counter.pk}))
        self.assertEqual(delete_response.status_code, 302)
        counter.refresh_from_db()
        self.assertTrue(counter.is_deleted)
        self.assertEqual(counter.deleted_by_id, self.moderator.id)
        self.assertTrue(AuditLog.objects.filter(action="moderation.soft_delete").exists())
        restore_response = self.client.post(reverse("thinking:moderation_counter_restore", kwargs={"pk": counter.pk}))
        self.assertEqual(restore_response.status_code, 302)
        counter.refresh_from_db()
        self.assertFalse(counter.is_deleted)
        self.assertIsNone(counter.deleted_by_id)
        self.assertTrue(AuditLog.objects.filter(action="moderation.restore").exists())

    def test_authenticated_user_can_report_thesis_and_audit_written(self):
        thesis = self.make_thesis(title="Reportable Thesis", author=self.user)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}),
            {"reason": "spam", "detail": "looks like spam"},
        )
        self.assertEqual(response.status_code, 302)
        report = ContentReport.objects.get(reporter=self.user, thesis=thesis)
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
        thesis = self.make_thesis(title="Duplicate Report Thesis", author=self.user)
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
        thesis = self.make_thesis(title="Deleted Thesis Report Block", author=self.user)
        thesis.soft_delete(actor=self.moderator)
        self.client.force_login(self.user)
        response = self.client.post(reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}), {"reason": "spam"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ContentReport.objects.filter(thesis=thesis).exists())

    def test_report_rate_limit_blocks_after_three_per_minute(self):
        theses = [self.make_thesis(title=f"Rate Report {idx}", author=self.user) for idx in range(4)]
        self.client.force_login(self.user)
        for thesis in theses:
            response = self.client.post(reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}), {"reason": "other"})
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
        thesis = self.make_thesis(title="Auto Pending Thesis", author=self.user)
        reporters = [self.make_user(prefix="auto_pending", username=f"auto_pending_{idx}") for idx in range(3)]
        for reporter in reporters:
            self.client.force_login(reporter)
            response = self.client.post(reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}), {"reason": "spam"})
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
        thesis = self.make_thesis(title="Auto Archived Thesis", author=self.user)
        for idx in range(5):
            reporter = self.make_user(prefix="auto_archived", username=f"auto_archived_{idx}")
            self.client.force_login(reporter)
            response = self.client.post(reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}), {"reason": "spam"})
            self.assertEqual(response.status_code, 302)
        thesis.refresh_from_db()
        self.assertEqual(thesis.status, ContentStatus.ARCHIVED)
        auto_logs = AuditLog.objects.filter(
            action="moderation.auto_status_change",
            metadata__target_type=ContentReport.TargetType.THESIS,
            metadata__target_id=str(thesis.pk),
        ).order_by("id")
        self.assertEqual(auto_logs.count(), 2)
        self.assertEqual(list(auto_logs.values_list("metadata__threshold_triggered", flat=True)), [3, 5])
        self.assertEqual(auto_logs[0].metadata.get("new_status"), ContentStatus.PENDING_REVIEW)
        self.assertEqual(auto_logs[1].metadata.get("new_status"), ContentStatus.ARCHIVED)

    def test_auto_moderation_is_idempotent_for_duplicate_open_report(self):
        thesis = self.make_thesis(title="Auto Idempotent Thesis", author=self.user)
        reporters = [self.make_user(prefix="auto_idempotent", username=f"auto_idempotent_{idx}") for idx in range(3)]
        for reporter in reporters:
            self.client.force_login(reporter)
            self.client.post(reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}), {"reason": "spam"})
        thesis.refresh_from_db()
        self.assertEqual(thesis.status, ContentStatus.PENDING_REVIEW)
        self.assertEqual(AuditLog.objects.filter(action="moderation.auto_status_change").count(), 1)
        self.client.force_login(reporters[0])
        duplicate = self.client.post(reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}), {"reason": "spam"})
        self.assertEqual(duplicate.status_code, 302)
        thesis.refresh_from_db()
        self.assertEqual(thesis.status, ContentStatus.PENDING_REVIEW)
        self.assertEqual(AuditLog.objects.filter(action="moderation.auto_status_change").count(), 1)

    def test_auto_moderation_helper_returns_false_for_deleted_target(self):
        thesis = self.make_thesis(title="Auto Deleted Guard", author=self.user)
        thesis.soft_delete(actor=self.moderator)
        changed = maybe_auto_moderate_after_report(target=thesis, request=None)
        self.assertFalse(changed)
        thesis.refresh_from_db()
        self.assertEqual(thesis.status, ContentStatus.ACTIVE)
        self.assertFalse(AuditLog.objects.filter(action="moderation.auto_status_change").exists())

    def test_auto_moderation_does_not_change_rejected_target(self):
        thesis = self.make_thesis(
            title="Auto Rejected Guard",
            author=self.user,
            status=ContentStatus.REJECTED,
        )
        for idx in range(5):
            reporter = self.make_user(prefix="auto_rejected", username=f"auto_rejected_{idx}")
            self.client.force_login(reporter)
            response = self.client.post(reverse("thinking:report_thesis", kwargs={"pk": thesis.pk}), {"reason": "spam"})
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
        thesis = self.make_thesis(title="Auto Counter Parent", author=self.user)
        argument = self.make_argument(thesis, order=1, body="arg")
        counter = self.make_counter(thesis, argument, body="counter", author=self.user)
        for idx in range(3):
            reporter = self.make_user(prefix="auto_counter", username=f"auto_counter_{idx}")
            self.client.force_login(reporter)
            response = self.client.post(reverse("thinking:report_counter", kwargs={"pk": counter.pk}), {"reason": "spam"})
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
        thesis_a = self.make_thesis(title="Bulk Resolve A", author=self.user)
        thesis_b = self.make_thesis(title="Bulk Resolve B", author=self.user, stance=Thesis.Stance.CON)
        argument = self.make_argument(thesis_a, order=1, body="arg")
        counter = self.make_counter(thesis_a, argument, body="counter", author=self.user)
        reporter_a = self.make_user(prefix="bulk_resolve_a", username="bulk_resolve_a")
        reporter_b = self.make_user(prefix="bulk_resolve_b", username="bulk_resolve_b")
        reporter_c = self.make_user(prefix="bulk_resolve_c", username="bulk_resolve_c")
        report_one = self.make_report(reporter=reporter_a, thesis=thesis_a, reason="spam")
        report_two = self.make_report(reporter=reporter_b, counter=counter, reason="other")
        report_three = self.make_report(reporter=reporter_c, thesis=thesis_b, reason="hate")
        self.client.force_login(self.moderator)
        response = self.client.post(
            reverse("thinking:moderation_reports_bulk"),
            {
                "action": "resolve",
                "report_ids": [str(report_one.id), str(report_two.id), str(report_three.id)],
                "next_query": "status=open&target_type=thesis&page=2",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            f"{reverse('thinking:moderation_panel')}?status=open&target_type=thesis&page=2",
        )
        for report in (report_one, report_two, report_three):
            report.refresh_from_db()
            self.assertEqual(report.status, ContentReport.Status.RESOLVED)
            self.assertEqual(report.resolved_by_id, self.moderator.id)
        self.assertEqual(
            AuditLog.objects.filter(
                action="content.report_resolved",
                metadata__report_id__in=[report_one.id, report_two.id, report_three.id],
            ).count(),
            3,
        )

    def test_operator_bulk_dismiss_updates_open_reports_and_logs_each(self):
        thesis = self.make_thesis(title="Bulk Dismiss Thesis", author=self.user)
        reporters = [
            self.make_user(prefix="bulk_dismiss", username=f"bulk_dismiss_{idx}")
            for idx in range(2)
        ]
        reports = [
            self.make_report(reporter=reporters[0], thesis=thesis, reason="spam"),
            self.make_report(reporter=reporters[1], thesis=thesis, reason="other"),
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
        thesis = self.make_thesis(title="Bulk Skip Thesis", author=self.user)
        reporters = [
            self.make_user(prefix="bulk_skip", username=f"bulk_skip_{idx}")
            for idx in range(3)
        ]
        reports = [
            self.make_report(reporter=reporters[idx], thesis=thesis, reason="spam")
            for idx in range(3)
        ]
        self.client.force_login(self.moderator)
        single_response = self.client.post(reverse("thinking:moderation_report_resolve", kwargs={"pk": reports[0].pk}))
        self.assertEqual(single_response.status_code, 302)
        before_bulk_logs = AuditLog.objects.filter(action="content.report_resolved").count()
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
        after_bulk_logs = AuditLog.objects.filter(action="content.report_resolved").count()
        self.assertEqual(after_bulk_logs - before_bulk_logs, 2)

    def test_non_moderator_cannot_use_bulk_report_endpoint(self):
        thesis = self.make_thesis(title="Bulk Forbidden Thesis", author=self.user)
        report = self.make_report(reporter=self.user, thesis=thesis, reason="spam")
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("thinking:moderation_reports_bulk"),
            {"action": "resolve", "report_ids": [str(report.id)]},
        )
        self.assertEqual(response.status_code, 403)
        report.refresh_from_db()
        self.assertEqual(report.status, ContentReport.Status.OPEN)

    def test_non_moderator_cannot_resolve_or_dismiss_report(self):
        thesis = self.make_thesis(title="Resolve Block Thesis", author=self.user)
        report = self.make_report(reporter=self.user, thesis=thesis, reason="spam")
        self.client.force_login(self.user)
        resolve_response = self.client.post(reverse("thinking:moderation_report_resolve", kwargs={"pk": report.pk}))
        dismiss_response = self.client.post(reverse("thinking:moderation_report_dismiss", kwargs={"pk": report.pk}))
        self.assertEqual(resolve_response.status_code, 403)
        self.assertEqual(dismiss_response.status_code, 403)
        report.refresh_from_db()
        self.assertEqual(report.status, ContentReport.Status.OPEN)

    def test_moderator_can_resolve_and_dismiss_reports_with_audit(self):
        thesis = self.make_thesis(title="Resolve Thesis", author=self.user)
        thesis_two = self.make_thesis(title="Dismiss Thesis", author=self.user)
        resolve_report = self.make_report(reporter=self.user, thesis=thesis, reason="spam")
        dismiss_report = self.make_report(reporter=self.user, thesis=thesis_two, reason="other")
        self.client.force_login(self.moderator)
        resolve_response = self.client.post(reverse("thinking:moderation_report_resolve", kwargs={"pk": resolve_report.pk}))
        dismiss_response = self.client.post(reverse("thinking:moderation_report_dismiss", kwargs={"pk": dismiss_report.pk}))
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


class AuditAdminTests(TestDataMixin, TestCase):
    def setUp(self):
        self.admin_user = self.make_user(prefix="auditadmin", username="auditadmin", superuser=True)
        self.normal_user = self.make_user(prefix="plainuser", username="plainuser")
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
        add_response = self.client.post(reverse("admin:thinking_auditlog_add"), {"action": "audit.try_add"})
        self.assertIn(add_response.status_code, {302, 403, 405})
        change_response = self.client.post(
            reverse("admin:thinking_auditlog_change", args=[self.audit_row.pk]),
            {"action": "audit.try_change"},
        )
        self.assertIn(change_response.status_code, {302, 403, 405})


class ClaimInferenceTests(TestDataMixin, TestCase):
    def setUp(self):
        self.user = self.make_user(prefix="inferuser", username="inferuser")
        self.thesis = self.make_thesis(title="Inference thesis", author=self.user)
        self.support_type, _created = ClaimRelationType.objects.get_or_create(
            code=ClaimRelationType.SUPPORT,
            defaults={"label": "Support"},
        )

    def create_claim(self, body: str) -> Claim:
        claim = self.make_claim(self.thesis, author=self.user, body=body)
        claim.refresh_from_db()
        return claim

    def test_inference_rule_application_creates_derived_claim(self):
        claim_a = self.create_claim("Smoking causes lung cancer")
        claim_b = self.create_claim("Lung cancer causes death")
        rebuild_thesis_inference_safe(thesis=self.thesis)
        inferred_claim = Claim.objects.get(body="smoking increases the risk of death")
        inference = ClaimInference.objects.get(inferred_claim=inferred_claim)
        self.assertEqual(
            {inference.source_claim_a_id, inference.source_claim_b_id},
            {claim_a.id, claim_b.id},
        )
        self.assertEqual(inference.rule.inferred_predicate, "increase-risk")
        self.assertTrue(
            ClaimRelation.objects.filter(
                source_claim=claim_a,
                target_claim=inferred_claim,
                relation_type__code=ClaimRelationType.SUPPORT,
            ).exists()
        )

    def test_contradiction_detection_flags_conflicting_predicates(self):
        claim_a = self.create_claim("Smoking causes cancer")
        claim_b = self.create_claim("Smoking prevents cancer")
        rebuild_thesis_inference_safe(thesis=self.thesis)
        contradiction = ClaimContradiction.objects.get()
        self.assertEqual({contradiction.claim_a_id, contradiction.claim_b_id}, {claim_a.id, claim_b.id})
        self.assertEqual(contradiction.contradiction_type, "causal-conflict")
        self.assertGreater(contradiction.confidence, 0.0)

    def test_support_closure_materializes_transitive_support_chain(self):
        claim_a = self.create_claim("Exercise supports heart health")
        claim_b = self.create_claim("Heart health supports longevity")
        claim_c = self.create_claim("Longevity supports wellbeing")
        ClaimRelation.objects.create(source_claim=claim_a, target_claim=claim_b, relation_type=self.support_type)
        ClaimRelation.objects.create(source_claim=claim_b, target_claim=claim_c, relation_type=self.support_type)
        rebuild_thesis_inference_safe(thesis=self.thesis)
        closure = ClaimSupportClosure.objects.get(source_claim=claim_a, target_claim=claim_c)
        self.assertEqual(closure.support_depth, 2)
        self.assertEqual(closure.confidence, 0.5)

    def test_inference_rules_seeded(self):
        self.create_claim("Masks prevent infection")
        rebuild_thesis_inference_safe(thesis=self.thesis)
        self.assertTrue(
            ClaimInferenceRule.objects.filter(name="causal-chain-increases-risk").exists()
        )
