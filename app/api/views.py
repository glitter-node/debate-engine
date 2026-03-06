import os

from api.middleware.ip_block import verify_request
from django.core.paginator import EmptyPage, Paginator
from django.db.models import Count
from django.http import (
    FileResponse,
    HttpRequest,
    HttpResponse,
    HttpResponseNotFound,
    JsonResponse,
)
from thinking.models import Thesis

import data.config._conf_ as const


def healthz(_: HttpRequest):
    return JsonResponse({"status": "ok"})


def theses(request: HttpRequest):
    if request.method not in ("GET", "HEAD"):
        return HttpResponse(
            "Method Not Allowed",
            status=405,
            content_type="text/plain; charset=utf-8",
        )

    sort = request.GET.get("sort", "active")
    qs = Thesis.objects.select_related("author").annotate(
        counter_count=Count("counters")
    )
    if sort == "new":
        qs = qs.order_by("-created_at")
    elif sort == "unanswered":
        qs = qs.filter(counter_count=0).order_by("-created_at")
    else:
        qs = qs.order_by("-counter_count", "-updated_at")

    page_raw = request.GET.get("page", "1")
    try:
        page = int(page_raw)
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1

    per_page = 20
    paginator = Paginator(qs, per_page)
    effective_page = page
    if paginator.num_pages == 0:
        effective_page = 1
    elif page > paginator.num_pages:
        effective_page = paginator.num_pages

    try:
        theses_page = paginator.page(effective_page).object_list
    except EmptyPage:
        theses_page = []

    payload = {
        "page": effective_page,
        "page_size": per_page,
        "num_pages": paginator.num_pages,
        "count": paginator.count,
        "theses": [
            {
                "id": thesis.id,
                "title": thesis.title,
                "stance": thesis.stance,
                "author": thesis.author.username,
                "counter_count": thesis.counter_count,
                "created_at": thesis.created_at.isoformat(),
                "updated_at": thesis.updated_at.isoformat(),
            }
            for thesis in theses_page
        ],
    }
    return JsonResponse(payload)


def favicon(_: HttpRequest):
    file_path = const.FAV_VRO
    if file_path and os.path.exists(file_path):
        return FileResponse(open(file_path, "rb"), content_type="image/x-icon")
    return HttpResponseNotFound()


def robots_txt(request: HttpRequest):
    if not verify_request(request):
        return HttpResponseNotFound()
    file_path = const.SGK_TXT
    if file_path and os.path.exists(file_path):
        return FileResponse(
            open(file_path, "rb"), content_type="text/plain; charset=utf-8"
        )
    return HttpResponseNotFound()


def sitemap_xml(request: HttpRequest):
    if not verify_request(request):
        return HttpResponseNotFound()
    file_path = const.SGK_XML
    if file_path and os.path.exists(file_path):
        return FileResponse(open(file_path, "rb"), content_type="application/xml")
    return HttpResponseNotFound()


def root(_: HttpRequest):
    return HttpResponse("OK", content_type="text/plain; charset=utf-8")
