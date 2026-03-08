# pylint: disable=no-member
"""
app.thinking.admin - Admin configuration for the "thinking" app.
"""

import json
from typing import cast

from django import forms
from django.contrib import admin
from django.forms import ModelChoiceField
from django.utils.html import escape, format_html

from .models import Argument, AuditLog, Counter, Thesis


class ArgumentInline(admin.TabularInline):
    model = Argument
    extra = 0


@admin.register(Argument)
class ArgumentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "thesis",
        "order",
        "short_body",
        "created_at",
    )
    list_filter = ("thesis", "created_at")
    search_fields = ("thesis__title", "body")
    ordering = ("thesis__id", "order", "id")
    list_select_related = ("thesis",)

    @admin.display(description="Body")
    def short_body(self, obj):
        text = " ".join((obj.body or "").split())
        if len(text) <= 80:
            return text
        return f"{text[:77]}..."


class CounterAdminForm(forms.ModelForm):
    class Meta:
        model = Counter
        fields = "__all__"


@admin.register(Thesis)
class ThesisAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "stance", "author", "created_at", "updated_at")
    search_fields = ("title", "summary", "author__username")
    list_filter = ("stance", "created_at")
    inlines = [ArgumentInline]


@admin.register(Counter)
class CounterAdmin(admin.ModelAdmin):
    form = CounterAdminForm
    list_display = ("id", "thesis", "target_argument", "author", "created_at")
    search_fields = ("body", "author__username")
    list_select_related = ("thesis", "target_argument", "author")

    def _target_argument_label(self, obj):
        thesis_title = "Missing thesis"
        if obj.thesis_id:
            try:
                thesis_title = obj.thesis.title
            except Thesis.DoesNotExist:
                thesis_title = f"Missing thesis #{obj.thesis_id}"
        return f"{thesis_title} · A{obj.order}"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "target_argument" and field is not None:
            model_field = cast(ModelChoiceField, field)
            model_field.label_from_instance = self._target_argument_label
        return field


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "created_at",
        "actor",
        "actor_role",
        "action",
        "target_model",
        "target_id",
        "ip_address",
    )
    list_filter = ("action", "actor_role", "created_at")
    search_fields = (
        "actor__username",
        "action",
        "target_model",
        "target_id",
        "ip_address",
    )
    ordering = ("-created_at",)
    list_per_page = 50
    date_hierarchy = "created_at"
    list_select_related = ("actor",)
    readonly_fields = (
        "id",
        "created_at",
        "actor",
        "actor_role",
        "action",
        "target_model",
        "target_id",
        "metadata_pretty",
        "ip_address",
        "user_agent",
    )
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        user = request.user
        return bool(user and user.is_active and user.is_staff)

    @admin.display(description="Metadata")
    def metadata_pretty(self, obj):
        pretty = json.dumps(
            obj.metadata or {}, indent=2, sort_keys=True, ensure_ascii=False
        )
        return format_html("<pre>{}</pre>", escape(pretty))
