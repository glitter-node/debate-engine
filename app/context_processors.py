from __future__ import annotations

from django.conf import settings


def app_version(_request):
    return {"APP_VERSION": settings.APP_VERSION}
