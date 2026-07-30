from decimal import Decimal
from typing import cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.core.forms import FlatForm, MeterReadingForm, SettlementForm
from apps.core.models import Contract, FeeCalculation, Flat, LedgerEntry
from apps.core.services.fees import save_settlement
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


# --- Create / action views -----------------------------------------------------
@login_required
def add_flat(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    if request.method == "POST":
        form = FlatForm(request.POST)
        if form.is_valid():
            flat = form.save(commit=False)
            flat.owner = user
            flat.save()
            messages.success(request, "Flat added.")
            return redirect("core:flats")
    else:
        form = FlatForm()
    return render(request, "core/form.html", {"form": form, "title": "Add flat"})


@login_required
def add_reading(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    if request.method == "POST":
        form = MeterReadingForm(request.POST, user=user)
        if form.is_valid():
            reading = form.save(commit=False)
            reading.owner = reading.meter.owner
            reading.flat = reading.meter.flat
            reading.save()
            messages.success(request, "Meter reading added.")
            return redirect("core:calculations")
    else:
        form = MeterReadingForm(user=user)
    return render(
        request, "core/form.html", {"form": form, "title": "Add meter reading"}
    )


@login_required
def run_settlement(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    if request.method == "POST":
        form = SettlementForm(request.POST, user=user)
        if form.is_valid():
            calc = save_settlement(
                form.cleaned_data["flat"],
                form.cleaned_data["period_start"],
                form.cleaned_data["period_end"],
            )
            messages.success(request, "Settlement computed and saved.")
            return redirect("core:calculation_detail", pk=calc.pk)
    else:
        form = SettlementForm(user=user)
    return render(
        request, "core/form.html", {"form": form, "title": "Run settlement"}
    )


@login_required
@require_POST
def delete_settlement(request: HttpRequest, pk: int) -> HttpResponse:
    user = cast(User, request.user)
    calc = get_object_or_404(FeeCalculation, pk=pk, owner=user)
    calc.delete()  # cascades to tenants and items
    messages.success(request, "Settlement deleted.")
    return redirect("core:calculations")