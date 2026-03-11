from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, JsonResponse


def app_version(_: HttpRequest):
    return JsonResponse({"version": settings.APP_VERSION})
