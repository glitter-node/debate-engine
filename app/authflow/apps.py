from django.apps import AppConfig


class AuthflowConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "authflow"

    def ready(self):
        from .env import validate_startup_settings

        validate_startup_settings()
