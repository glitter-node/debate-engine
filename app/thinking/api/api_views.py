from django.http import JsonResponse

from ..models import Thesis
from .serializers import thesis_summary


def thesis_list_json(_request):
    payload = [thesis_summary(thesis) for thesis in Thesis.objects.select_related("author")[:20]]
    return JsonResponse({"items": payload})
