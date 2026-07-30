from decimal import Decimal
from typing import cast

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Contract, FeeCalculation, Flat, LedgerEntry
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


@login_required
def contracts(request: HttpRequest) -> HttpResponse:
    """All tenancy contracts with active/expired status (port of contr.php)."""
    user = cast(User, request.user)
    today = timezone.now().date()
    rows = (
        Contract.objects.filter(owner=user)
        .select_related("flat", "room")
        .order_by("-contract_start")
    )
    return render(request, "core/contracts.html", {"contracts": rows, "today": today})


@login_required
def records(request: HttpRequest) -> HttpResponse:
    """Recent financial ledger entries (port of records.php)."""
    user = cast(User, request.user)
    entries = (
        LedgerEntry.objects.filter(owner=user)
        .select_related("flat")
        .order_by("-record_date")[:100]
    )
    return render(request, "core/records.html", {"entries": entries})


@login_required
def calculations(request: HttpRequest) -> HttpResponse:
    """Saved utility settlements (port of fees.php)."""
    user = cast(User, request.user)
    calcs = (
        FeeCalculation.objects.filter(owner=user)
        .select_related("flat")
        .order_by("-period_start")
    )
    return render(request, "core/calculations.html", {"calculations": calcs})


@login_required
def calculation_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Per-tenant breakdown of a single settlement."""
    user = cast(User, request.user)
    calc = get_object_or_404(
        FeeCalculation.objects.select_related("flat"), pk=pk, owner=user
    )
    tenants = []
    for tenant in calc.tenants.all():
        items = list(tenant.items.all())
        tenants.append(
            {
                "tenant": tenant,
                "items": items,
                "total": sum((i.value for i in items), start=Decimal(0)),
            }
        )
    return render(
        request, "core/calculation_detail.html", {"calc": calc, "tenants": tenants}
    )


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness probe used by Docker/monitoring."""
    return JsonResponse({"status": "ok"})
