import calendar
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, F, Sum
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, UpdateView

from apps.accounts.models import MailSettings, User
from apps.core.forms import (
    AdHocEmailForm,
    AdminFeeForm,
    AdminFeePriceForm,
    ContractForm,
    EmailTemplateForm,
    FeeCreateForm,
    FlatForm,
    LedgerEntryForm,
    MailSettingsForm,
    MeterDefinitionForm,
    MeterFieldsForm,
    MeterPriceForm,
    MeterReadingForm,
    RoomForm,
    SecuritySettingsForm,
    SettlementForm,
    WishlistForm,
    WishlistReplyForm,
)
from apps.core.models import (
    AdminFee,
    AdminFeePrice,
    Contract,
    ContractInvite,
    EmailLog,
    EmailTemplate,
    FeeCalculation,
    FeeCalculationTenant,
    Flat,
    LedgerEntry,
    MeterDefinition,
    MeterPrice,
    MeterReading,
    Room,
    TaxDue,
    WishlistItem,
    WishlistMessage,
)
from apps.core.services.counters import counters_matrix
from apps.core.services.estimate import estimate_flat_readings
from apps.core.services.fees import save_settlement
from apps.core.services.forecast import (
    contract_ratio,
    forecast_income,
    forecast_income_total,
    rent_arrears,
    rent_due_status,
)
from apps.core.services.mortgage import is_mortgaged, mortgage_schedule
from apps.core.services.stats import (
    RoomOccupancy,
    expected_income,
    inventory_state,
    monthly_income_series,
    occupancy,
)
from apps.core.services.tax import monthly_tax, tax_for_year, tax_table
from apps.core.tasks import email_settlement_task


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """Inventory overview (port of the legacy dashboard/getInvState)."""
    user = cast(User, request.user)
    now = timezone.now()
    year, month = now.year, now.month

    # Tax: last completed month as the headline, year-to-date as fine print.
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    period_label = f"{prev_month:02d}/{prev_year}"
    tax_month = monthly_tax(user, prev_year, prev_month)
    tax_ytd = tax_for_year(user, prev_year)

    # Payment status drives the badge on the tax card.
    if tax_month.paid_date is not None:
        tax_status = "paid"
    elif now.date() > tax_month.deadline:
        tax_status = "overdue"
    else:
        tax_status = "pending"

    # Income headline = actually recorded taxable income for the completed month
    # (same source as the Ewidencja page and the chart bars, so they agree).
    prev_start = timezone.make_aware(datetime(prev_year, prev_month, 1))
    prev_end = timezone.make_aware(
        datetime(prev_year + 1, 1, 1)
        if prev_month == 12
        else datetime(prev_year, prev_month + 1, 1)
    )
    year_start = timezone.make_aware(datetime(prev_year, 1, 1))
    income_month = LedgerEntry.objects.filter(
        owner=user,
        record_date__gte=prev_start,
        record_date__lt=prev_end,
        kind=LedgerEntry.Kind.RENT,
    ).aggregate(t=Sum("amount_in_taxable"))["t"] or Decimal(0)
    income_ytd = LedgerEntry.objects.filter(
        owner=user,
        record_date__gte=year_start,
        record_date__lt=prev_end,
        kind=LedgerEntry.Kind.RENT,
    ).aggregate(t=Sum("amount_in_taxable"))["t"] or Decimal(0)

    # Chart: recorded income (przychód) and net after ryczałt (dochód), each with
    # its own bar heights so the toggle can switch between the two views.
    income_series = monthly_income_series(user, months=12)
    net_values = [
        p.value
        - (p.value * Decimal("0.085")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        for p in income_series
    ]
    net_peak = max(net_values, default=Decimal(0))
    income_chart = [
        {
            "label": p.label,
            "income": p.value,
            "income_pct": p.pct,
            "net": net,
            "net_pct": int(net / net_peak * 90) if net_peak > 0 else 0,
            "tax_pct": int(round((p.value - net) / p.value * 100)) if p.value > 0 else 0,
        }
        for p, net in zip(income_series, net_values, strict=True)
    ]
    count = len(income_series)
    income_avg = (
        sum((p.value for p in income_series), Decimal(0)) / count
        if count
        else Decimal(0)
    )
    net_avg = sum(net_values, Decimal(0)) / count if count else Decimal(0)

    # Arrears alert: unpaid tax (overdue completed months) + overdue rent.
    tax_arrears = [
        m
        for months in tax_table(user).values()
        for m in months
        if m.tax > 0 and m.paid_date is None and m.deadline < now.date()
    ]
    tax_arrears_total = sum((m.tax for m in tax_arrears), 0)
    rent_arrear_items = rent_arrears(user, months=12)
    rent_arrears_total = sum((i.amount for i in rent_arrear_items), Decimal(0))
    arrears_total = Decimal(tax_arrears_total) + rent_arrears_total

    return render(
        request,
        "core/dashboard.html",
        {
            "stats": inventory_state(user),
            "tax_year": prev_year,
            "period_label": period_label,
            "tax_month": tax_month,
            "tax_ytd": tax_ytd,
            "tax_status": tax_status,
            "income_month": income_month,
            "income_ytd": income_ytd,
            "income_chart": income_chart,
            "income_avg": income_avg,
            "net_avg": net_avg,
            "tax_arrears": tax_arrears,
            "tax_arrears_total": tax_arrears_total,
            "rent_arrears": rent_arrear_items,
            "rent_arrears_total": rent_arrears_total,
            "arrears_total": arrears_total,
            "has_arrears": bool(tax_arrears or rent_arrear_items),
        },
    )


@login_required
def flats(request: HttpRequest) -> HttpResponse:
    """List of flats with expected income and occupancy (port of flats.php)."""
    user = cast(User, request.user)
    rows = []
    for flat in Flat.objects.filter(owner=user):
        occ: list[RoomOccupancy] = occupancy(flat)
        total = len(occ)
        occupied = sum(1 for o in occ if o.status != "danger")
        capacity = Room.objects.filter(flat=flat).aggregate(b=Sum("beds"))["b"] or 0
        rows.append(
            {
                "flat": flat,
                "income": expected_income(flat),
                "occupancy": occ,
                "occupied": occupied,
                "total_rooms": total,
                "occupancy_pct": int(occupied / total * 100) if total else 0,
                "capacity": capacity,
                "mortgaged": is_mortgaged(flat),
            }
        )
    return render(request, "core/flats.html", {"rows": rows})


@login_required
def flat_fees(request: HttpRequest, pk: int) -> HttpResponse:
    """Manage a flat's fees: fixed admin fees and metered utilities, with the
    full price history of each."""
    user = cast(User, request.user)
    flat = get_object_or_404(Flat, pk=pk, owner=user)

    fees = []
    for fee in AdminFee.objects.filter(flat=flat).order_by("id"):
        prices = list(fee.prices.order_by(F("price_date").desc(nulls_last=True), "-id"))
        fees.append(
            {"fee": fee, "current": prices[0] if prices else None, "prices": prices}
        )

    meters = []
    for meter in MeterDefinition.objects.filter(flat=flat).order_by("id"):
        mprices = list(
            meter.prices.order_by(F("price_date").desc(nulls_last=True), "-id")
        )
        meters.append(
            {
                "meter": meter,
                "current": mprices[0] if mprices else None,
                "prices": mprices,
            }
        )

    return render(
        request,
        "core/flat_fees.html",
        {
            "flat": flat,
            "fees": fees,
            "meters": meters,
            "units": MeterDefinition.Unit.choices,
        },
    )


@login_required
def fee_add(request: HttpRequest) -> HttpResponse:
    """Unified 'add fee' form for both admin fees and metered utilities."""
    user = cast(User, request.user)
    if request.method == "POST":
        form = FeeCreateForm(request.POST, user=user)
        if form.is_valid():
            data = form.cleaned_data
            flat = data["flat"]
            kind = data["kind"]
            amount = data["amount"]
            pdate = data.get("price_date")
            if kind == "admin":
                fee = AdminFee.objects.create(
                    owner=user,
                    flat=flat,
                    title=data["title"],
                    is_individual=data.get("is_individual", False),
                )
                admin_dt = (
                    timezone.make_aware(datetime(pdate.year, pdate.month, pdate.day))
                    if pdate
                    else None
                )
                AdminFeePrice.objects.create(
                    owner=user,
                    flat=flat,
                    admin_fee=fee,
                    price=amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                    price_date=admin_dt,
                )
            else:
                meter = MeterDefinition.objects.create(
                    owner=user, flat=flat, name=data["title"], unit=kind
                )
                MeterPrice.objects.create(
                    owner=user, flat=flat, meter=meter, price=amount, price_date=pdate
                )
            messages.success(request, "Opłata dodana.")
            return redirect("core:flat_fees", pk=flat.pk)
    else:
        initial = {}
        flat_id = request.GET.get("flat")
        if flat_id:
            initial["flat"] = flat_id
        form = FeeCreateForm(user=user, initial=initial)
    return render(
        request,
        "core/fee_form.html",
        {
            "form": form,
            "flats": Flat.objects.filter(owner=user).order_by("city", "street"),
            "title": "Dodaj opłatę",
        },
    )


@login_required
@require_POST
def edit_fee(request: HttpRequest, pk: int, fee_id: int) -> HttpResponse:
    user = cast(User, request.user)
    fee = get_object_or_404(AdminFee, pk=fee_id, owner=user, flat_id=pk)
    form = AdminFeeForm(request.POST, instance=fee)
    if form.is_valid():
        form.save()
        messages.success(request, "Opłata zaktualizowana.")
    else:
        messages.error(request, "Nie udało się zapisać opłaty.")
    return redirect("core:flat_fees", pk=pk)


@login_required
@require_POST
def delete_fee(request: HttpRequest, pk: int, fee_id: int) -> HttpResponse:
    user = cast(User, request.user)
    fee = get_object_or_404(AdminFee, pk=fee_id, owner=user, flat_id=pk)
    fee.delete()
    messages.success(request, "Opłata usunięta.")
    return redirect("core:flat_fees", pk=pk)


@login_required
@require_POST
def add_fee_price(request: HttpRequest, pk: int, fee_id: int) -> HttpResponse:
    """Record a new price for a fee — a new dated entry keeps the full history."""
    user = cast(User, request.user)
    fee = get_object_or_404(AdminFee, pk=fee_id, owner=user, flat_id=pk)
    form = AdminFeePriceForm(request.POST)
    if form.is_valid():
        price = form.save(commit=False)
        price.owner = user
        price.flat = fee.flat
        price.admin_fee = fee
        price.save()
        messages.success(request, "Nowa stawka zapisana.")
    else:
        messages.error(request, "Podaj poprawną kwotę i datę.")
    return redirect("core:flat_fees", pk=pk)


@login_required
@require_POST
def delete_fee_price(request: HttpRequest, pk: int, price_id: int) -> HttpResponse:
    user = cast(User, request.user)
    price = get_object_or_404(AdminFeePrice, pk=price_id, owner=user, flat_id=pk)
    price.delete()
    messages.success(request, "Stawka usunięta.")
    return redirect("core:flat_fees", pk=pk)


@login_required
@require_POST
def flat_meter_edit(request: HttpRequest, pk: int, meter_id: int) -> HttpResponse:
    user = cast(User, request.user)
    meter = get_object_or_404(MeterDefinition, pk=meter_id, owner=user, flat_id=pk)
    form = MeterFieldsForm(request.POST, instance=meter)
    if form.is_valid():
        form.save()
        messages.success(request, "Licznik zaktualizowany.")
    else:
        messages.error(request, "Nie udało się zapisać licznika.")
    return redirect("core:flat_fees", pk=pk)


@login_required
@require_POST
def flat_meter_delete(request: HttpRequest, pk: int, meter_id: int) -> HttpResponse:
    user = cast(User, request.user)
    meter = get_object_or_404(MeterDefinition, pk=meter_id, owner=user, flat_id=pk)
    meter.delete()
    messages.success(request, "Licznik usunięty.")
    return redirect("core:flat_fees", pk=pk)


@login_required
@require_POST
def add_meter_price(request: HttpRequest, pk: int, meter_id: int) -> HttpResponse:
    user = cast(User, request.user)
    meter = get_object_or_404(MeterDefinition, pk=meter_id, owner=user, flat_id=pk)
    form = MeterPriceForm(request.POST)
    if form.is_valid():
        price = form.save(commit=False)
        price.owner = user
        price.flat = meter.flat
        price.meter = meter
        price.save()
        messages.success(request, "Nowa stawka zapisana.")
    else:
        messages.error(request, "Podaj poprawną stawkę i datę.")
    return redirect("core:flat_fees", pk=pk)


@login_required
@require_POST
def delete_meter_price(request: HttpRequest, pk: int, price_id: int) -> HttpResponse:
    user = cast(User, request.user)
    price = get_object_or_404(MeterPrice, pk=price_id, owner=user, flat_id=pk)
    price.delete()
    messages.success(request, "Stawka usunięta.")
    return redirect("core:flat_fees", pk=pk)


@login_required
def contracts(request: HttpRequest) -> HttpResponse:
    """Tenancy contracts split into active and past (port of contr.php)."""
    user = cast(User, request.user)
    today = timezone.now().date()
    rows = (
        Contract.objects.filter(owner=user)
        .select_related("flat", "room")
        .order_by("-contract_start")
    )
    active: list[Contract] = []
    past: list[Contract] = []
    for c in rows:
        is_active = bool(
            c.contract_start
            and c.contract_end
            and c.contract_start <= today <= c.contract_end
        )
        (active if is_active else past).append(c)
    return render(
        request,
        "core/contracts.html",
        {"active": active, "past": past, "today": today},
    )


@login_required
def records(request: HttpRequest) -> HttpResponse:
    """Monthly income/expense ledger with per-flat summary (port of records.php)."""
    user = cast(User, request.user)
    today = timezone.now().date()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
    except (TypeError, ValueError):
        year, month = today.year, today.month
    if not 1 <= month <= 12:
        year, month = today.year, today.month

    month_start = timezone.make_aware(datetime(year, month, 1))
    month_end = timezone.make_aware(
        datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    )

    flats = list(Flat.objects.filter(owner=user).order_by("city", "street"))
    flat_ids = {f.pk for f in flats}
    try:
        selected_flat_id: int | None = int(request.GET.get("flat", ""))
    except (TypeError, ValueError):
        selected_flat_id = None
    if selected_flat_id not in flat_ids:
        selected_flat_id = None

    # Group by the billing month (miesiąc rozliczeniowy), NOT the payment date —
    # a payment settles a specific month even if it arrived early or late.
    entry_qs = LedgerEntry.objects.filter(
        owner=user, billing_period=date(year, month, 1)
    )
    if selected_flat_id:
        entry_qs = entry_qs.filter(flat_id=selected_flat_id)
    entries = list(
        entry_qs.select_related("flat", "room", "contract").order_by("record_date")
    )
    rent_entries = [e for e in entries if e.kind != LedgerEntry.Kind.FEE]
    fee_entries = [e for e in entries if e.kind == LedgerEntry.Kind.FEE]

    zero = Decimal(0)
    per_flat: dict[int, dict[str, Any]] = {}
    tot_income = zero
    for e in rent_entries:
        row = per_flat.setdefault(
            e.flat_id,
            {"flat": e.flat, "income": zero},
        )
        income = e.amount_in_taxable or zero
        row["income"] += income
        tot_income += income

    summary_rows = sorted(per_flat.values(), key=lambda r: str(r["flat"]))

    # Private rental is taxed by ryczałt only (8.5% of taxable income); costs are
    # not deductible, so result = income − tax. Same rule per flat and in total.
    def _ryczalt(income: Decimal) -> Decimal:
        return max(
            Decimal(0),
            (income * Decimal("0.085")).quantize(Decimal("1"), rounding=ROUND_HALF_UP),
        )

    for row in summary_rows:
        row["ryczalt"] = _ryczalt(row["income"])
        row["wynik"] = row["income"] - row["ryczalt"]

    ryczalt = _ryczalt(tot_income)
    wynik = tot_income - ryczalt

    # Expected rent (getForeRecords): active contracts with no payment recorded
    # this month, prorated by the active-days ratio, with a due-date status.
    today = timezone.localdate()
    days_in_month = calendar.monthrange(year, month)[1]
    period_first = date(year, month, 1)
    period_last = date(year, month, days_in_month)
    paid_contract_ids = {e.contract_id for e in rent_entries if e.contract_id is not None}
    forecast_rows = []
    active_contracts = (
        Contract.objects.filter(
            owner=user,
            contract_start__lte=period_last,
            contract_end__gte=period_first,
        )
        .select_related("flat", "room")
        .order_by("flat__city", "contract_number")
    )
    if selected_flat_id:
        active_contracts = active_contracts.filter(flat_id=selected_flat_id)
    for ct in active_contracts:
        if ct.pk in paid_contract_ids or ct.price is None:
            continue
        amount = (ct.price * contract_ratio(ct, year, month)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        pay_day = min(ct.payment_day or 1, days_in_month)
        forecast_rows.append(
            {
                "contract": ct,
                "amount": amount,
                "record_date": date(year, month, pay_day),
                "status": rent_due_status(
                    period_first, period_last, ct.payment_day, today
                ),
            }
        )

    # --- Pozostałe opłaty: fees from saved settlements ------------------------
    # Fees are billed in arrears: in month M the tenant pays for month M-1
    # (rent, by contrast, is billed in advance for the current month).
    fee_year = year - 1 if month == 1 else year
    fee_month = 12 if month == 1 else month - 1
    fee_contracts = Contract.objects.filter(owner=user)
    if selected_flat_id:
        fee_contracts = fee_contracts.filter(flat_id=selected_flat_id)
    contracts_by_num = {
        c.contract_number: c
        for c in fee_contracts.select_related("flat", "room")
        if c.contract_number
    }
    paid_fee_contract_ids = {e.contract_id for e in fee_entries if e.contract_id}
    fee_rows: list[dict[str, Any]] = []
    fee_calcs = (
        FeeCalculation.objects.filter(owner=user)
        .select_related("flat")
        .prefetch_related("tenants__items")
    )
    if selected_flat_id:
        fee_calcs = fee_calcs.filter(flat_id=selected_flat_id)
    for calc in fee_calcs:
        midpoint = calc.period_start + (calc.period_end - calc.period_start) / 2
        if (midpoint.year, midpoint.month) != (fee_year, fee_month):
            continue
        for tenant in calc.tenants.all():
            total = sum((it.value for it in tenant.items.all()), Decimal(0))
            if total <= 0:
                continue
            contract = contracts_by_num.get(tenant.contract_number)
            if contract and contract.pk in paid_fee_contract_ids:
                continue
            fee_rows.append(
                {
                    "flat": calc.flat,
                    "tenant": tenant,
                    "contract": contract,
                    "amount": total,
                }
            )

    prev_month = (month - 1) or 12
    prev_year = year - 1 if month == 1 else year
    next_month = (month % 12) + 1
    next_year = year + 1 if month == 12 else year

    context = {
        "year": year,
        "month": month,
        "month_label": _PL_MONTHS[month - 1],
        "period_start": month_start,
        "period_end": month_end,
        "rent_entries": rent_entries,
        "fee_entries": fee_entries,
        "fee_rows": fee_rows,
        "fee_period_label": f"{_PL_MONTHS[fee_month - 1]} {fee_year}",
        "forecast_rows": forecast_rows,
        "summary_rows": summary_rows,
        "flats": flats,
        "selected_flat_id": selected_flat_id,
        "billing_ym": f"{year:04d}-{month:02d}",
        "totals": {
            "income": tot_income,
            "ryczalt": ryczalt,
            "wynik": wynik,
        },
        "prev": {"year": prev_year, "month": prev_month},
        "next": {"year": next_year, "month": next_month},
    }
    return render(request, "core/records.html", context)


_PL_MONTHS = [
    "Styczeń",
    "Luty",
    "Marzec",
    "Kwiecień",
    "Maj",
    "Czerwiec",
    "Lipiec",
    "Sierpień",
    "Wrzesień",
    "Październik",
    "Listopad",
    "Grudzień",
]


@login_required
def calculations(request: HttpRequest) -> HttpResponse:
    """Saved utility settlements (port of fees.php)."""
    user = cast(User, request.user)
    calcs = list(
        FeeCalculation.objects.filter(owner=user)
        .select_related("flat")
        .annotate(tenants_count=Count("tenants"))
        .order_by("-period_start")
    )
    for calc in calcs:
        # Label by the month holding the majority of the period: the midpoint's
        # month is the month that contains most of a contiguous interval.
        midpoint = calc.period_start + (calc.period_end - calc.period_start) / 2
        calc.period_label = f"{_PL_MONTHS[midpoint.month - 1]} {midpoint.year}"  # type: ignore[attr-defined]
    flats = Flat.objects.filter(owner=user).order_by("city", "street")
    return render(
        request,
        "core/calculations.html",
        {"calculations": calcs, "flats": flats},
    )


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


# --- Tenant portal -------------------------------------------------------------
def _tenant_contracts(user: User) -> "list[Contract]":
    """Active (non-deleted) contracts linked to this tenant account."""
    return list(
        Contract.objects.filter(tenant_user=user).select_related("flat", "room", "owner")
    )


@login_required
def portal(request: HttpRequest) -> HttpResponse:
    """Tenant home: overview of their contracts and landlord contact."""
    user = cast(User, request.user)
    contracts = _tenant_contracts(user)
    return render(request, "core/portal/dashboard.html", {"contracts": contracts})


@login_required
def portal_settlements(request: HttpRequest) -> HttpResponse:
    """Utility settlements addressed to this tenant (matched by contract number)."""
    user = cast(User, request.user)
    numbers = [c.contract_number for c in _tenant_contracts(user) if c.contract_number]
    rows = []
    if numbers:
        tenants = (
            FeeCalculationTenant.objects.filter(contract_number__in=numbers)
            .select_related("calculation", "flat")
            .prefetch_related("items")
            .order_by("-calculation__period_start")
        )
        for t in tenants:
            items = list(t.items.all())
            rows.append(
                {
                    "calc": t.calculation,
                    "flat": t.flat,
                    "items": items,
                    "total": sum((i.value for i in items), Decimal("0")),
                }
            )
    return render(request, "core/portal/settlements.html", {"rows": rows})


@login_required
def portal_payments(request: HttpRequest) -> HttpResponse:
    """Payment history recorded against this tenant's contracts."""
    user = cast(User, request.user)
    contracts = _tenant_contracts(user)
    entries = (
        LedgerEntry.objects.filter(contract__in=contracts)
        .select_related("flat", "room", "contract")
        .order_by("-record_date")
    )
    return render(
        request, "core/portal/payments.html", {"entries": entries, "contracts": contracts}
    )


# --- Landlord: tenant invites --------------------------------------------------
@login_required
def contract_invites(request: HttpRequest, pk: int) -> HttpResponse:
    """Manage tenant invites for one contract (landlord)."""
    user = cast(User, request.user)
    contract = get_object_or_404(Contract, pk=pk, owner=user)
    invites = contract.invites.select_related("accepted_by").all()
    link = None
    latest = invites.filter(accepted_by__isnull=True).first()
    if latest:
        link = request.build_absolute_uri(
            f"{reverse('accounts:register_tenant')}?invite={latest.token}"
        )
    return render(
        request,
        "core/contract_invites.html",
        {"contract": contract, "invites": invites, "link": link, "latest": latest},
    )


@login_required
@require_POST
def create_invite(request: HttpRequest, pk: int) -> HttpResponse:
    """Create a fresh tenant invite token for a contract (landlord)."""
    user = cast(User, request.user)
    contract = get_object_or_404(Contract, pk=pk, owner=user)
    ContractInvite.objects.create(contract=contract, email=contract.email or "")
    messages.success(request, "Utworzono zaproszenie dla najemcy.")
    return redirect("core:contract_invites", pk=contract.pk)


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
            messages.success(request, "Mieszkanie dodane.")
            return redirect("core:flats")
    else:
        form = FlatForm()
    return render(
        request,
        "core/flat_form.html",
        {
            "form": form,
            "title": "Dodaj mieszkanie",
            "subtitle": "Dane nowego mieszkania",
        },
    )


def _reading_flat_data(flats: Any, post: Any) -> list[dict[str, Any]]:
    """Build per-flat meter rows, pre-filling values from POST on re-render and
    exposing the most recent reading as a hint."""
    data = []
    for flat in flats:
        rows = []
        for meter in flat.meters.all():
            # Meta ordering is (meter, read_date asc), so prefetched readings end
            # with the most recent one — reuse the cache instead of a new query.
            readings = list(meter.readings.all())
            last = readings[-1] if readings else None
            rows.append(
                {
                    "meter": meter,
                    "value": post.get(f"value_{meter.pk}", "") if post else "",
                    "estimated": post.get(f"estimated_{meter.pk}") == "1" if post else False,
                    "last": last,
                }
            )
        data.append({"flat": flat, "rows": rows})
    return data


@login_required
def add_reading(request: HttpRequest) -> HttpResponse:
    user = cast(User, request.user)
    flats = list(
        Flat.objects.filter(owner=user)
        .prefetch_related("meters", "meters__readings")
        .order_by("city", "street")
    )
    selected_flat_id: int | None = None
    read_date_value = timezone.localdate().isoformat()

    if request.method == "POST":
        post = request.POST
        read_date_value = post.get("read_date", read_date_value)
        flat = next((f for f in flats if str(f.pk) == post.get("flat")), None)
        read_date = parse_date(post.get("read_date", "") or "")
        errors: list[str] = []
        if flat is None:
            errors.append("Wybierz mieszkanie.")
        if read_date is None:
            errors.append("Podaj poprawną datę odczytu.")

        created = 0
        if flat is not None and read_date is not None:
            selected_flat_id = flat.pk
            for meter in flat.meters.all():
                raw = post.get(f"value_{meter.pk}", "").strip()
                if not raw:
                    continue
                try:
                    value = Decimal(raw.replace(",", "."))
                except (InvalidOperation, ValueError):
                    errors.append(f"Nieprawidłowa wartość dla „{meter.name}”.")
                    continue
                is_estimated = post.get(f"estimated_{meter.pk}") == "1"
                MeterReading.objects.update_or_create(
                    owner=user,
                    flat=flat,
                    meter=meter,
                    read_date=read_date,
                    defaults={"value": value, "is_estimated": is_estimated},
                )
                created += 1
            if not errors and created == 0:
                errors.append("Wprowadź wartość przynajmniej jednego licznika.")

        if not errors:
            messages.success(request, f"Zapisano odczyty ({created}).")
            return redirect("core:counters")
        for err in errors:
            messages.error(request, err)
        flat_data = _reading_flat_data(flats, post)
    else:
        flat_data = _reading_flat_data(flats, None)

    return render(
        request,
        "core/add_reading.html",
        {
            "title": "Dodaj odczyt",
            "flat_data": flat_data,
            "selected_flat_id": selected_flat_id,
            "read_date_value": read_date_value,
        },
    )


@login_required
def estimate_readings(request: HttpRequest) -> JsonResponse:
    """Return estimated meter values for a flat as JSON (used by "Oszacuj")."""
    user = cast(User, request.user)
    flat = get_object_or_404(Flat, pk=request.GET.get("flat"), owner=user)

    read_date = parse_date(request.GET.get("read_date", "") or "") or timezone.localdate()
    try:
        months = int(request.GET.get("months", "12"))
    except ValueError:
        months = 12
    months = max(6, min(12, months))

    estimates = estimate_flat_readings(flat, read_date, months)
    return JsonResponse(
        {"estimates": {str(mid): f"{value:.3f}" for mid, value in estimates.items()}}
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
            if form.cleaned_data["email_tenants"]:
                email_settlement_task.delay(calc.pk)
                messages.success(
                    request, "Rozliczenie zapisane; e-maile do najemców są wysyłane."
                )
            else:
                messages.success(request, "Rozliczenie obliczone i zapisane.")
            return redirect("core:calculation_detail", pk=calc.pk)
    else:
        form = SettlementForm(user=user)
    return render(
        request, "core/settlement_form.html", {"form": form, "title": "Nowe rozliczenie"}
    )


@login_required
@require_POST
def delete_settlement(request: HttpRequest, pk: int) -> HttpResponse:
    user = cast(User, request.user)
    calc = get_object_or_404(FeeCalculation, pk=pk, owner=user)
    calc.delete()  # cascades to tenants and items
    messages.success(request, "Rozliczenie usunięte.")
    return redirect("core:calculations")


@login_required
@require_POST
def email_settlement(request: HttpRequest, pk: int) -> HttpResponse:
    """Queue tenant settlement emails to run in the background."""
    user = cast(User, request.user)
    calc = get_object_or_404(FeeCalculation, pk=pk, owner=user)
    email_settlement_task.delay(calc.pk)
    messages.success(request, "E-maile do najemców są wysyłane w tle.")
    return redirect("core:calculation_detail", pk=calc.pk)


@login_required
def rooms(request: HttpRequest) -> HttpResponse:
    """Rooms grouped by flat. ``?flat=<id>`` narrows to a single flat."""
    user = cast(User, request.user)
    flats = Flat.objects.filter(owner=user)
    flat_id = request.GET.get("flat")
    if flat_id:
        flats = flats.filter(pk=flat_id)

    groups = []
    for flat in flats:
        occ = {o.room.id: o for o in occupancy(flat)}
        room_rows = []
        for room in Room.objects.filter(flat=flat).order_by("room_no"):
            match = occ.get(room.id)
            room_rows.append(
                {
                    "room": room,
                    "status": match.status if match else "danger",
                    "tenant": match.tenant_name if match else "",
                }
            )
        groups.append({"flat": flat, "rooms": room_rows})

    return render(
        request, "core/rooms.html", {"groups": groups, "single": bool(flat_id)}
    )


@login_required
def tax(request: HttpRequest) -> HttpResponse:
    """Monthly lump-sum tax table (port of getTaxList/getTaxByMonth), one year per page."""
    user = cast(User, request.user)
    table = tax_table(user)  # dict[year, list[MonthlyTax]], newest year first
    years = list(table.keys())
    if not years:
        return render(
            request,
            "core/tax.html",
            {"year": None, "months": [], "years": []},
        )
    try:
        selected = int(request.GET.get("year", years[0]))
    except (TypeError, ValueError):
        selected = years[0]
    if selected not in table:
        selected = years[0]

    idx = years.index(selected)
    newer_year = years[idx - 1] if idx > 0 else None  # list is newest-first
    older_year = years[idx + 1] if idx < len(years) - 1 else None
    months = table[selected]
    return render(
        request,
        "core/tax.html",
        {
            "year": selected,
            "months": months,
            "years": years,
            "newer_year": newer_year,
            "older_year": older_year,
            "year_taxable": sum((m.taxable for m in months), Decimal(0)),
            "year_tax": sum(m.tax for m in months),
        },
    )


@login_required
@require_POST
def confirm_tax_payment(request: HttpRequest, year: int, month: int) -> HttpResponse:
    """Record that a month's lump-sum tax has been paid (full amount, today)."""
    user = cast(User, request.user)
    mt = monthly_tax(user, year, month)
    if mt.tax <= 0:
        messages.info(request, "Brak podatku do zapłaty w tym miesiącu.")
        return redirect(f"{reverse('core:tax')}?year={year}")
    TaxDue.objects.update_or_create(
        owner=user,
        period=f"{month:02d}/{year}",
        defaults={"tax_amount": mt.tax, "tax_date": timezone.now()},
    )
    messages.success(request, f"Zapisano zapłatę podatku za {month:02d}/{year}.")
    return redirect(f"{reverse('core:tax')}?year={year}")


@login_required
@require_POST
def delete_tax_payment(request: HttpRequest, pk: int) -> HttpResponse:
    """Remove a recorded tax payment (mark the month unpaid again)."""
    user = cast(User, request.user)
    due = get_object_or_404(TaxDue, pk=pk, owner=user)
    try:
        year = int(due.period.split("/")[1])
    except (IndexError, ValueError):
        year = timezone.now().year
    due.delete()
    messages.success(request, "Usunięto zapłatę podatku.")
    return redirect(f"{reverse('core:tax')}?year={year}")


@login_required
def user_settings(request: HttpRequest) -> HttpResponse:
    """Account settings: change password and configure outgoing SMTP mail."""
    user = cast(User, request.user)
    mail, _ = MailSettings.objects.get_or_create(user=user)
    pwd_form = PasswordChangeForm(user)
    mail_form = MailSettingsForm(instance=mail)
    sec_form = SecuritySettingsForm(instance=user)
    if request.method == "POST":
        if "save_password" in request.POST:
            pwd_form = PasswordChangeForm(user, request.POST)
            if pwd_form.is_valid():
                pwd_form.save()
                update_session_auth_hash(request, pwd_form.user)
                messages.success(request, "Hasło zostało zmienione.")
                return redirect("core:settings")
        elif "save_mail" in request.POST:
            mail_form = MailSettingsForm(request.POST, instance=mail)
            if mail_form.is_valid():
                mail_form.save()
                messages.success(request, "Ustawienia poczty zapisane.")
                return redirect("core:settings")
        elif "save_security" in request.POST:
            sec_form = SecuritySettingsForm(request.POST, instance=user)
            if sec_form.is_valid():
                sec_form.save()
                request.session.set_expiry(
                    sec_form.cleaned_data["session_timeout_minutes"] * 60
                )
                messages.success(request, "Ustawienia bezpieczeństwa zapisane.")
                return redirect("core:settings")
    return render(
        request,
        "core/settings.html",
        {"pwd_form": pwd_form, "mail_form": mail_form, "sec_form": sec_form},
    )


@login_required
def wishlist(request: HttpRequest) -> HttpResponse:
    """User feedback page: submit problems/wishes and track their status."""
    user = cast(User, request.user)
    wish_form = WishlistForm()
    if request.method == "POST":
        wish_form = WishlistForm(request.POST)
        if wish_form.is_valid():
            item = wish_form.save(commit=False)
            item.user = user
            item.save()
            messages.success(request, "Zgłoszenie zostało wysłane. Dziękujemy!")
            return redirect("core:wishlist")
    items = (
        WishlistItem.objects.filter(user=user)
        .prefetch_related("messages", "messages__author")
    )
    return render(
        request,
        "core/wishlist.html",
        {
            "wish_form": wish_form,
            "wishlist": items,
            "reply_form": WishlistReplyForm(),
        },
    )


@login_required
@require_POST
def wishlist_reply(request: HttpRequest, pk: int) -> HttpResponse:
    """Add a follow-up message to one of the user's own wishlist items."""
    user = cast(User, request.user)
    item = get_object_or_404(WishlistItem, pk=pk, user=user)
    form = WishlistReplyForm(request.POST)
    if form.is_valid():
        WishlistMessage.objects.create(
            item=item,
            author=user,
            from_staff=False,
            body=form.cleaned_data["body"],
        )
        # Reopen a resolved item when the user comes back with more to say.
        if item.status in (WishlistItem.Status.DONE, WishlistItem.Status.CLOSED):
            item.status = WishlistItem.Status.OPEN
            item.save(update_fields=["status", "updated"])
        messages.success(request, "Odpowiedź została dodana.")
    return redirect("core:wishlist")


@login_required
def communication(request: HttpRequest) -> HttpResponse:
    """Landlord communication hub: templates, ad-hoc send and history."""
    from apps.core.services.mailer import ensure_default_templates

    user = cast(User, request.user)
    ensure_default_templates(user)
    templates = EmailTemplate.objects.filter(owner=user)
    logs = EmailLog.objects.filter(owner=user)[:50]
    adhoc_form = AdHocEmailForm(user=user)
    return render(
        request,
        "core/communication.html",
        {
            "templates": templates,
            "logs": logs,
            "adhoc_form": adhoc_form,
        },
    )


@login_required
def email_template_add(request: HttpRequest) -> HttpResponse:
    """Create a new e-mail template owned by the landlord."""
    from apps.core.services.mailer import TEMPLATE_TAGS, preview_context

    user = cast(User, request.user)
    form = EmailTemplateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        tpl = form.save(commit=False)
        tpl.owner = user
        tpl.save()
        messages.success(request, "Szablon zapisany.")
        return redirect(f"{reverse('core:communication')}#tab-templates")
    return render(
        request,
        "core/email_template_form.html",
        {
            "form": form,
            "title": "Nowy szablon",
            "tags": TEMPLATE_TAGS,
            "samples": preview_context(user),
        },
    )


@login_required
def email_template_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit one of the landlord's own e-mail templates."""
    from apps.core.services.mailer import TEMPLATE_TAGS, preview_context

    user = cast(User, request.user)
    tpl = get_object_or_404(EmailTemplate, pk=pk, owner=user)
    form = EmailTemplateForm(request.POST or None, instance=tpl)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Szablon zaktualizowany.")
        return redirect(f"{reverse('core:communication')}#tab-templates")
    return render(
        request,
        "core/email_template_form.html",
        {
            "form": form,
            "title": "Edytuj szablon",
            "tags": TEMPLATE_TAGS,
            "samples": preview_context(user),
        },
    )


@login_required
@require_POST
def email_template_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete one of the landlord's own e-mail templates."""
    user = cast(User, request.user)
    tpl = get_object_or_404(EmailTemplate, pk=pk, owner=user)
    tpl.delete()
    messages.success(request, "Szablon usunięty.")
    return redirect(f"{reverse('core:communication')}#tab-templates")


@login_required
@require_POST
def send_adhoc(request: HttpRequest) -> HttpResponse:
    """Send an ad-hoc notification to all active tenants of a flat (BCC)."""
    from apps.core.services.mailer import render_text
    from apps.core.tasks import send_flat_broadcast_task

    user = cast(User, request.user)
    form = AdHocEmailForm(request.POST, user=user)
    if form.is_valid():
        flat = form.cleaned_data["flat"]
        tpl = form.cleaned_data.get("template")
        ctx = {"flat": str(flat), "owner_name": user.get_full_name() or user.get_username()}
        subject = render_text(form.cleaned_data["subject"], ctx)
        body = render_text(form.cleaned_data["body"], ctx)
        send_flat_broadcast_task.delay(
            user.pk, flat.pk, subject, body, tpl.pk if tpl else None
        )
        messages.success(request, "Wiadomość została skierowana do wysyłki.")
        return redirect(f"{reverse('core:communication')}#tab-send")
    # Re-render the hub with the invalid form so errors are visible.
    templates = EmailTemplate.objects.filter(owner=user)
    logs = EmailLog.objects.filter(owner=user)[:50]
    return render(
        request,
        "core/communication.html",
        {
            "templates": templates,
            "logs": logs,
            "adhoc_form": form,
            "open_tab": "tab-send",
        },
    )


@login_required
def counters(request: HttpRequest) -> HttpResponse:
    """Per-flat meter matrix: reading / usage / cost per date (port of counters.php)."""
    user = cast(User, request.user)
    return render(request, "core/counters.html", {"flats": counters_matrix(user)})


@login_required
@require_POST
def delete_readings_on_date(request: HttpRequest, flat_id: int) -> HttpResponse:
    """Delete every meter reading of a flat taken on a given date."""
    user = cast(User, request.user)
    flat = get_object_or_404(Flat, pk=flat_id, owner=user)
    read_date = request.POST.get("date")
    if read_date:
        MeterReading.objects.filter(
            owner=user, flat=flat, read_date=read_date
        ).delete()
        messages.success(request, "Odczyty z tego dnia zostały usunięte.")
    return redirect("core:counters")


@login_required
def meter_readings(request: HttpRequest, pk: int) -> HttpResponse:
    """All readings for a single meter."""
    user = cast(User, request.user)
    meter = get_object_or_404(MeterDefinition, pk=pk, owner=user)
    readings = MeterReading.objects.filter(meter=meter).order_by("-read_date")
    return render(
        request, "core/meter_readings.html", {"meter": meter, "readings": readings}
    )


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
    template_name = "core/flat_form.html"
    success_url = reverse_lazy("core:flats")
    extra_context = {
        "title": "Edytuj mieszkanie",
        "subtitle": "Dane mieszkania i zarządzanie opłatami",
    }

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        ctx = super().get_context_data(**kwargs)  # type: ignore[misc]
        ctx["secondary_link"] = reverse_lazy(
            "core:flat_fees", args=[self.object.pk]
        )
        ctx["secondary_label"] = "Zarządzaj opłatami →"
        return ctx


class FlatDelete(_OwnerSoftDelete):
    model = Flat
    success_url = reverse_lazy("core:flats")
    extra_context = {"title": "Usuń mieszkanie"}


class RoomCreate(_OwnerCreate):
    model = Room
    form_class = RoomForm
    template_name = "core/room_form.html"
    success_url = reverse_lazy("core:rooms")
    extra_context = {
        "title": "Dodaj pokój",
        "subtitle": "Nowy pokój w mieszkaniu",
    }


class RoomUpdate(_OwnerUpdate):
    model = Room
    form_class = RoomForm
    template_name = "core/room_form.html"
    success_url = reverse_lazy("core:rooms")
    extra_context = {
        "title": "Edytuj pokój",
        "subtitle": "Dane pokoju i warunki najmu",
    }


class RoomDelete(_OwnerSoftDelete):
    model = Room
    success_url = reverse_lazy("core:rooms")
    extra_context = {"title": "Usuń pokój"}


class ContractCreate(_OwnerCreate):
    model = Contract
    form_class = ContractForm
    success_url = reverse_lazy("core:contracts")
    extra_context = {"title": "Dodaj umowę"}


class ContractUpdate(_OwnerUpdate):
    model = Contract
    form_class = ContractForm
    success_url = reverse_lazy("core:contracts")
    extra_context = {"title": "Edytuj umowę"}


class ContractDelete(_OwnerSoftDelete):
    model = Contract
    success_url = reverse_lazy("core:contracts")
    extra_context = {"title": "Usuń umowę"}


class RecordCreate(_OwnerCreate):
    model = LedgerEntry
    form_class = LedgerEntryForm
    template_name = "core/record_form.html"
    success_url = reverse_lazy("core:records")
    extra_context = {
        "title": "Dodaj wpis",
        "subtitle": "Nowy wpis do ewidencji — przychód z najmu",
    }

    def get_initial(self) -> dict[str, Any]:
        """Pre-fill fields from query params (e.g. confirming an expected rent)."""
        initial = cast(dict[str, Any], super().get_initial())  # type: ignore[misc]
        allowed = {
            "flat",
            "room",
            "contract",
            "kind",
            "short_desc",
            "record_date",
            "billing_period",
            "amount_in_taxable",
        }
        for key in allowed & set(self.request.GET):
            initial[key] = self.request.GET[key]
        return initial


class RecordUpdate(_OwnerUpdate):
    model = LedgerEntry
    form_class = LedgerEntryForm
    template_name = "core/record_form.html"
    success_url = reverse_lazy("core:records")
    extra_context = {
        "title": "Edytuj wpis",
        "subtitle": "Zmień dane wpisu w ewidencji",
    }


class RecordDelete(_OwnerHardDelete):
    model = LedgerEntry
    success_url = reverse_lazy("core:records")
    extra_context = {"title": "Usuń wpis"}


class MeterCreate(_OwnerCreate):
    model = MeterDefinition
    form_class = MeterDefinitionForm
    success_url = reverse_lazy("core:counters")
    extra_context = {"title": "Dodaj licznik"}


class MeterUpdate(_OwnerUpdate):
    model = MeterDefinition
    form_class = MeterDefinitionForm
    success_url = reverse_lazy("core:counters")
    extra_context = {"title": "Edytuj licznik"}


class MeterDelete(_OwnerHardDelete):
    model = MeterDefinition
    success_url = reverse_lazy("core:counters")
    extra_context = {"title": "Usuń licznik"}


class ReadingUpdate(_OwnerUpdate):
    model = MeterReading
    form_class = MeterReadingForm
    success_url = reverse_lazy("core:counters")
    extra_context = {"title": "Edytuj odczyt"}


class ReadingDelete(_OwnerHardDelete):
    model = MeterReading
    success_url = reverse_lazy("core:counters")
    extra_context = {"title": "Usuń odczyt"}