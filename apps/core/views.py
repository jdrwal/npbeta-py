from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render


def home(request: HttpRequest) -> HttpResponse:
    """Temporary landing page confirming the skeleton runs."""
    return render(request, "core/home.html")


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness probe used by Docker/monitoring."""
    return JsonResponse({"status": "ok"})
