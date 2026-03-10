"""
app.thinking.apps - App configuration for the "thinking" app.
"""

from django.apps import AppConfig


class ThinkingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "thinking"

    def ready(self):
        from . import signals  # noqa: F401
