"""
app.thinking.forms - Forms for the "thinking" app.
"""

from django import forms
from django.forms import inlineformset_factory

from .models import Argument, Counter, Thesis


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
        fields = ["target_argument", "body"]
        widgets = {
            "target_argument": forms.Select(attrs={"class": "select"}),
            "body": forms.Textarea(attrs={"class": "textarea", "rows": 6}),
        }
