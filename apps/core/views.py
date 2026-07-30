from typing import cast

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render

from apps.accounts.models import User
from apps.core.models import Flat
from apps.core.services.stats import (
    RoomOccupancy,
    expected_income,
    inventory_state,
    occupancy,
)


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """Inventory overview (port of the legacy dashboard/getInvState)."""
    user = cast(User, request.user)
    return render(
        request,
        "core/dashboard.html",
        {"stats": inventory_state(user)},
    )


@login_required
def flats(request: HttpRequest) -> HttpResponse:
    """List of flats with expected income and occupancy (port of flats.php)."""
    user = cast(User, request.user)
    rows = []
    for flat in Flat.objects.filter(owner=user):
        occ: list[RoomOccupancy] = occupancy(flat)
        rows.append(
            {
                "flat": flat,
                "income": expected_income(flat),
                "occupancy": occ,
                "occupied": sum(1 for o in occ if o.status != "danger"),
                "total_rooms": len(occ),
            }
        )
    return render(request, "core/flats.html", {"rows": rows})


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness probe used by Docker/monitoring."""
    return JsonResponse({"status": "ok"})
