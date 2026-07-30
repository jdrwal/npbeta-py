from decimal import Decimal
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, UpdateView

from apps.accounts.models import User
from apps.core.forms import (
    ContractForm,
    FlatForm,
    LedgerEntryForm,
    MeterReadingForm,
    RoomForm,
    SettlementForm,
)
from apps.core.models import (
    Contract,
    FeeCalculation,
    Flat,
    LedgerEntry,
    Room,
)
from apps.core.services.fees import save_settlement
from apps.core.services.forecast import forecast_income, forecast_income_total
from apps.core.services.mortgage import mortgage_schedule
from apps.core.services.stats import (
    RoomOccupancy,
    expected_income,
    inventory_state,
    occupancy,
)
from apps.core.services.tax import tax_for_year, tax_table


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """Inventory overview (port of the legacy dashboard/getInvState)."""
    user = cast(User, request.user)
    return render(
        request,
        "core/dashboard.html",
        {
            "stats": inventory_state(user),
            "tax_year": timezone.now().year,
            "tax_ytd": tax_for_year(user, timezone.now().year),
            "forecast_income": forecast_income_total(
                user, timezone.now().year, timezone.now().month
            ),
        },
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


@login_required
def rooms(request: HttpRequest) -> HttpResponse:
    """List of rooms across the owner's flats, with CRUD links."""
    user = cast(User, request.user)
    room_list = (
        Room.objects.filter(owner=user).select_related("flat").order_by("flat", "room_no")
    )
    return render(request, "core/rooms.html", {"rooms": room_list})


@login_required
def tax(request: HttpRequest) -> HttpResponse:
    """Monthly lump-sum tax table (port of getTaxList/getTaxByMonth)."""
    user = cast(User, request.user)
    return render(request, "core/tax.html", {"table": tax_table(user)})


@login_required
def forecast(request: HttpRequest) -> HttpResponse:
    """Forecasted rent income for this month + estimated mortgage schedule."""
    user = cast(User, request.user)
    today = timezone.now().date()
    rows = [
        {"flat": flat, "income": forecast_income(flat, today.year, today.month)}
        for flat in Flat.objects.filter(owner=user)
    ]
    return render(
        request,
        "core/forecast.html",
        {
            "year": today.year,
            "month": today.month,
            "rows": rows,
            "total": forecast_income_total(user, today.year, today.month),
            "schedule": mortgage_schedule(user, months=12),
        },
    )


# --- Generic owner-scoped CRUD -------------------------------------------------
class _OwnerQuerysetMixin(LoginRequiredMixin):
    """Restrict object access to the logged-in owner."""

    model: type

    def get_queryset(self) -> Any:
        return self.model.objects.filter(owner=self.request.user)  # type: ignore[attr-defined]


class _UserFormMixin:
    """Pass the current user to the form so FK choices can be scoped."""

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()  # type: ignore[misc]
        kwargs["user"] = self.request.user  # type: ignore[attr-defined]
        return kwargs


class _OwnerCreate(_OwnerQuerysetMixin, _UserFormMixin, CreateView):
    template_name = "core/form.html"

    def form_valid(self, form: Any) -> HttpResponse:
        form.instance.owner = self.request.user
        return super().form_valid(form)


class _OwnerUpdate(_OwnerQuerysetMixin, _UserFormMixin, UpdateView):
    template_name = "core/form.html"


class _OwnerSoftDelete(_OwnerQuerysetMixin, DeleteView):
    template_name = "core/confirm_delete.html"

    def form_valid(self, form: Any) -> HttpResponse:
        self.object.soft_delete()
        return HttpResponseRedirect(self.get_success_url())


class _OwnerHardDelete(_OwnerQuerysetMixin, DeleteView):
    template_name = "core/confirm_delete.html"


class FlatUpdate(_OwnerUpdate):
    model = Flat
    form_class = FlatForm
    success_url = reverse_lazy("core:flats")
    extra_context = {"title": "Edit flat"}


class FlatDelete(_OwnerSoftDelete):
    model = Flat
    success_url = reverse_lazy("core:flats")
    extra_context = {"title": "Delete flat"}


class RoomCreate(_OwnerCreate):
    model = Room
    form_class = RoomForm
    success_url = reverse_lazy("core:rooms")
    extra_context = {"title": "Add room"}


class RoomUpdate(_OwnerUpdate):
    model = Room
    form_class = RoomForm
    success_url = reverse_lazy("core:rooms")
    extra_context = {"title": "Edit room"}


class RoomDelete(_OwnerSoftDelete):
    model = Room
    success_url = reverse_lazy("core:rooms")
    extra_context = {"title": "Delete room"}


class ContractCreate(_OwnerCreate):
    model = Contract
    form_class = ContractForm
    success_url = reverse_lazy("core:contracts")
    extra_context = {"title": "Add contract"}


class ContractUpdate(_OwnerUpdate):
    model = Contract
    form_class = ContractForm
    success_url = reverse_lazy("core:contracts")
    extra_context = {"title": "Edit contract"}


class ContractDelete(_OwnerSoftDelete):
    model = Contract
    success_url = reverse_lazy("core:contracts")
    extra_context = {"title": "Delete contract"}


class RecordCreate(_OwnerCreate):
    model = LedgerEntry
    form_class = LedgerEntryForm
    success_url = reverse_lazy("core:records")
    extra_context = {"title": "Add record"}


class RecordUpdate(_OwnerUpdate):
    model = LedgerEntry
    form_class = LedgerEntryForm
    success_url = reverse_lazy("core:records")
    extra_context = {"title": "Edit record"}


class RecordDelete(_OwnerHardDelete):
    model = LedgerEntry
    success_url = reverse_lazy("core:records")
    extra_context = {"title": "Delete record"}