"""
app.thinking.forms - Forms for the "thinking" app.
"""

from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory

from .models import Argument, Claim, ClaimEvidence, ClaimRelationType, Counter, Thesis


class ThesisForm(forms.ModelForm):
    class Meta:
        model = Thesis
        fields = ["title", "summary", "stance"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "input", "maxlength": "200"}),
            "summary": forms.Textarea(attrs={"class": "textarea", "rows": 6}),
            "stance": forms.Select(attrs={"class": "select"}),
        }


ArgumentFormSet = inlineformset_factory(
    Thesis,
    Argument,
    fields=["order", "body"],
    extra=3,
    can_delete=False,
    widgets={
        "order": forms.NumberInput(attrs={"class": "input", "min": 1}),
        "body": forms.Textarea(attrs={"class": "textarea", "rows": 4}),
    },
)


class CounterForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_argument"].label_from_instance = self._target_argument_label

    @staticmethod
    def _target_argument_label(argument: Argument) -> str:
        return f"A{argument.order}"

    class Meta:
        model = Counter
        fields = ["target_argument", "parent_counter", "body"]
        widgets = {
            "target_argument": forms.Select(attrs={"class": "select"}),
            "parent_counter": forms.HiddenInput(),
            "body": forms.Textarea(attrs={"class": "textarea", "rows": 6}),
        }


class ClaimForm(forms.ModelForm):
    target_claim = forms.ModelChoiceField(
        queryset=Claim.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "select"}),
    )
    relation_type = forms.ModelChoiceField(
        queryset=ClaimRelationType.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "select"}),
    )

    class Meta:
        model = Claim
        fields = ["body", "status"]
        widgets = {
            "body": forms.Textarea(attrs={"class": "textarea", "rows": 6}),
            "status": forms.Select(attrs={"class": "select"}),
        }

    def __init__(self, *args, thesis=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.thesis = thesis
        if thesis is not None:
            self.fields["target_claim"].queryset = Claim.objects.filter(thesis=thesis)
        self.fields["relation_type"].queryset = ClaimRelationType.objects.all()

    def clean(self):
        cleaned = super().clean() or {}
        target_claim = cleaned.get("target_claim")
        relation_type = cleaned.get("relation_type")
        if target_claim and relation_type is None:
            self.add_error(
                "relation_type",
                "Select a relation type for linked claims.",
            )
        if relation_type and target_claim is None:
            self.add_error(
                "target_claim",
                "Select a target claim to create a relation.",
            )
        if (
            self.thesis is not None
            and target_claim
            and target_claim.thesis_id != self.thesis.id
        ):
            self.add_error(
                "target_claim",
                "Target claim must belong to the same thesis.",
            )
        return cleaned


class ClaimEvidenceForm(forms.ModelForm):
    class Meta:
        model = ClaimEvidence
        fields = [
            "url",
            "title",
            "source_label",
            "citation_count",
            "trust_score",
            "excerpt",
        ]
        widgets = {
            "url": forms.URLInput(attrs={"class": "input"}),
            "title": forms.TextInput(attrs={"class": "input", "maxlength": "200"}),
            "source_label": forms.TextInput(
                attrs={"class": "input", "maxlength": "120"}
            ),
            "citation_count": forms.NumberInput(attrs={"class": "input", "min": 0}),
            "trust_score": forms.NumberInput(
                attrs={"class": "input", "min": 0, "max": 5, "step": 0.1}
            ),
            "excerpt": forms.Textarea(attrs={"class": "textarea", "rows": 4}),
        }


class ClaimEditForm(forms.ModelForm):
    class Meta:
        model = Claim
        fields = ["body", "status"]
        widgets = {
            "body": forms.Textarea(attrs={"class": "textarea", "rows": 6}),
            "status": forms.Select(attrs={"class": "select"}),
        }


class ClaimMergeSelectionForm(forms.Form):
    source_claim = forms.ModelChoiceField(
        queryset=Claim.objects.none(),
        widget=forms.Select(attrs={"class": "select"}),
    )
    target_claim = forms.ModelChoiceField(
        queryset=Claim.objects.none(),
        widget=forms.Select(attrs={"class": "select"}),
    )
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "textarea", "rows": 4}),
    )

    def __init__(self, *args, search_query="", **kwargs):
        super().__init__(*args, **kwargs)
        qs = Claim.objects.select_related("thesis", "author").exclude(
            canonical_record__isnull=False
        )
        if search_query:
            qs = qs.filter(
                Q(body__icontains=search_query)
                | Q(thesis__title__icontains=search_query)
                | Q(author__username__icontains=search_query)
            )
        self.fields["source_claim"].queryset = qs
        self.fields["target_claim"].queryset = qs

    def clean(self):
        cleaned = super().clean() or {}
        source_claim = cleaned.get("source_claim")
        target_claim = cleaned.get("target_claim")
        if source_claim and target_claim and source_claim.pk == target_claim.pk:
            self.add_error("target_claim", "Source and target claims must differ.")
        return cleaned


class ClaimDuplicateReviewForm(forms.Form):
    claim_a = forms.ModelChoiceField(
        queryset=Claim.objects.none(),
        widget=forms.Select(attrs={"class": "select"}),
    )
    claim_b = forms.ModelChoiceField(
        queryset=Claim.objects.none(),
        widget=forms.Select(attrs={"class": "select"}),
    )
    decision = forms.ChoiceField(
        choices=(
            ("merge", "Merge"),
            ("ignore", "Ignore"),
        ),
        widget=forms.Select(attrs={"class": "select"}),
    )
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "textarea", "rows": 4}),
    )

    def __init__(self, *args, search_query="", **kwargs):
        super().__init__(*args, **kwargs)
        qs = Claim.objects.select_related("thesis", "author").exclude(
            canonical_record__isnull=False
        )
        if search_query:
            qs = qs.filter(
                Q(body__icontains=search_query)
                | Q(thesis__title__icontains=search_query)
                | Q(author__username__icontains=search_query)
            )
        self.fields["claim_a"].queryset = qs
        self.fields["claim_b"].queryset = qs

    def clean(self):
        cleaned = super().clean() or {}
        claim_a = cleaned.get("claim_a")
        claim_b = cleaned.get("claim_b")
        if claim_a and claim_b and claim_a.pk == claim_b.pk:
            self.add_error("claim_b", "Duplicate review requires two distinct claims.")
        return cleaned
