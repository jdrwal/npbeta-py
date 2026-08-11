"""Utility-fee settlement — faithful port of the legacy PHP algorithm.

Reproduces ``calculateFeesByPeriod`` / ``getCounterPriceByDay`` /
``getAdminPriceByDay`` from the old ``np`` class. The settlement is computed
day-by-day: for every day in the period the daily cost and consumption of each
meter and each admin fee is apportioned across the tenants active that day,
summed over the period and rounded.

Parity notes (must match the legacy engine, incl. its quirks):

- Tenant activity and flat capacity ignore the soft-delete flag (the legacy
  SQL had no ``deleted`` filter) — hence ``all_objects``.
- A price row with a NULL effective date is treated as "effective from the
  beginning" (legacy stored ``0000-00-00``, imported as NULL).
- Counter prices apply strictly before the day (a price dated on the reading
  day counts for the next period); admin prices apply on/before the day.
- Admin ``period_length`` is the number of days in the day's calendar month,
  with February always 28 (legacy passed the month as the year to
  ``cal_days_in_month``); reproduced with a fixed non-leap reference year.
- Non-individual admin fees are split across ``min(active + 2, capacity)``.
- Rounding is half-up: counter value→2 / usage→3, admin value→2 / usage→4.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.models import (
    AdminFee,
    AdminFeeInvoice,
    AdminFeePrice,
    Contract,
    FeeCalculation,
    FeeCalculationItem,
    FeeCalculationTenant,
    Flat,
    Fund,
    MeterDefinition,
    MeterPrice,
    MeterReading,
    Room,
)
from apps.core.services.funds import fund_rate_for

_NON_LEAP_YEAR = 2001  # any non-leap year: reproduces the legacy "February = 28" quirk
_CENT = Decimal("0.01")
_MILLI = Decimal("0.001")
_TEN_THOUSANDTH = Decimal("0.0001")


@dataclass
class FeeLine:
    """One settled fee line for a tenant (mirrors a legacy ``duefees`` row)."""

    contract_id: int
    fee_type: str  # "Counter" | "Admin" | "Fund"
    name: str
    usage: Decimal
    value: Decimal


def _round(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _active_contracts(flat: Flat, day: date) -> list[Contract]:
    """Contracts active on ``day`` (start strictly before, end on/after).

    An open-ended contract (``contract_end`` is ``None``) counts as active.
    """
    return list(
        Contract.all_objects.filter(flat=flat, contract_start__lt=day).filter(
            Q(contract_end__gte=day) | Q(contract_end__isnull=True)
        )
    )


def _capacity(flat: Flat) -> int:
    return Room.all_objects.filter(flat=flat).aggregate(total=Sum("beds"))["total"] or 0


def _counter_price(meter: MeterDefinition, day: date) -> Decimal | None:
    """Latest meter unit price effective strictly before ``day``."""
    dated = (
        MeterPrice.objects.filter(meter=meter, price_date__lt=day)
        .order_by("-price_date")
        .first()
    )
    if dated is None:
        dated = MeterPrice.objects.filter(meter=meter, price_date__isnull=True).first()
    return dated.price if dated else None


def _admin_price(admin_fee: AdminFee, day: date) -> Decimal | None:
    """Latest admin-fee price effective on/before ``day``."""
    dated = (
        AdminFeePrice.objects.filter(admin_fee=admin_fee, price_date__date__lte=day)
        .order_by("-price_date")
        .first()
    )
    if dated is None:
        dated = AdminFeePrice.objects.filter(
            admin_fee=admin_fee, price_date__isnull=True
        ).first()
    return dated.price if dated else None


def _invoice_amount(admin_fee: AdminFee, day: date) -> Decimal | None:
    """Invoiced amount recorded for the calendar month containing ``day``."""
    row = AdminFeeInvoice.objects.filter(
        admin_fee=admin_fee, period__year=day.year, period__month=day.month
    ).first()
    return row.amount if row is not None else None


def _daily_counter(
    meter: MeterDefinition, day: date, tenants: int
) -> tuple[Decimal, Decimal] | None:
    """Return (consumption_per_tenant, cost_per_tenant) for ``meter`` on ``day``."""
    price = _counter_price(meter, day)
    if price is None:
        return None
    beg = (
        MeterReading.objects.filter(meter=meter, read_date__lt=day)
        .order_by("-read_date")
        .first()
    )
    end = (
        MeterReading.objects.filter(meter=meter, read_date__gte=day)
        .order_by("read_date")
        .first()
    )
    if beg is None or end is None:
        return None
    period_days = (end.read_date - beg.read_date).days
    if period_days <= 0:
        return None
    daily_consumption = (end.value - beg.value) / period_days
    consumption_per_tenant = daily_consumption / tenants
    cost_per_tenant = consumption_per_tenant * price
    return consumption_per_tenant, cost_per_tenant


def _daily_admin(
    admin_fee: AdminFee, day: date, tenants: int, capacity: int
) -> tuple[Decimal, Decimal] | None:
    """Return (usage_share, cost_per_tenant) for ``admin_fee`` on ``day``."""
    # Invoice fees carry a per-month amount (from the received invoice) split
    # equally among the active tenants, like a metered utility whose total we
    # simply know instead of reading. Uses the real month length, not the
    # legacy February=28 quirk.
    if admin_fee.is_invoice:
        amount = _invoice_amount(admin_fee, day)
        if amount is None:
            return None
        period_length = calendar.monthrange(day.year, day.month)[1]
        daily_cost = amount / period_length
        return Decimal(1) / tenants / period_length, daily_cost / tenants
    price = _admin_price(admin_fee, day)
    if price is None:
        return None
    period_length = calendar.monthrange(_NON_LEAP_YEAR, day.month)[1]
    daily_cost = price / period_length
    if admin_fee.is_individual:
        cost_per_tenant = daily_cost
        usage_share = Decimal(1) / period_length
    else:
        splitter = min(tenants + 2, capacity) if capacity else tenants
        cost_per_tenant = daily_cost / splitter
        usage_share = Decimal(1) / tenants / period_length
    return usage_share, cost_per_tenant


def calculate_fees(flat: Flat, period_start: date, period_end: date) -> list[FeeLine]:
    """Compute per-tenant fee lines for ``flat`` over the inclusive period."""
    meters = list(MeterDefinition.objects.filter(flat=flat).order_by("id"))
    admin_fees = list(AdminFee.objects.filter(flat=flat).order_by("id"))
    funds = list(
        Fund.objects.filter(flat=flat, start_date__lte=period_end)
        .filter(Q(end_date__gte=period_start) | Q(end_date__isnull=True))
        .order_by("id")
    )
    capacity = _capacity(flat)
    # tenant_id -> {("Counter", meter_id): [usage, value], ("Admin", fee_id): [...]}
    dues: dict[int, dict[tuple[str, int], list[Decimal]]] = {}
    counter_names = {m.id: m.name for m in meters}
    admin_titles = {a.id: a.title for a in admin_fees}

    day = period_start
    while day <= period_end:
        active = _active_contracts(flat, day)
        if active:
            tenants = len(active)
            for contract in active:
                dues.setdefault(contract.id, {})

            for meter in meters:
                result = _daily_counter(meter, day, tenants)
                if result is None:
                    continue
                consumption, cost = result
                for contract in active:
                    acc = dues[contract.id].setdefault(
                        ("Counter", meter.id), [Decimal(0), Decimal(0)]
                    )
                    acc[0] += consumption
                    acc[1] += cost

            for admin_fee in admin_fees:
                result = _daily_admin(admin_fee, day, tenants, capacity)
                if result is None:
                    continue
                usage_share, cost = result
                for contract in active:
                    acc = dues[contract.id].setdefault(
                        ("Admin", admin_fee.id), [Decimal(0), Decimal(0)]
                    )
                    acc[0] += usage_share
                    acc[1] += cost
        day += timedelta(days=1)

    lines: list[FeeLine] = []
    for contract_id, fees in dues.items():
        for meter in meters:
            usage, value = fees.get(("Counter", meter.id), (Decimal(0), Decimal(0)))
            lines.append(
                FeeLine(
                    contract_id=contract_id,
                    fee_type="Counter",
                    name=counter_names[meter.id],
                    usage=_round(usage, _MILLI),
                    value=_round(value, _CENT),
                )
            )
        for admin_fee in admin_fees:
            usage, value = fees.get(("Admin", admin_fee.id), (Decimal(0), Decimal(0)))
            lines.append(
                FeeLine(
                    contract_id=contract_id,
                    fee_type="Admin",
                    name=admin_titles[admin_fee.id],
                    usage=_round(usage, _TEN_THOUSANDTH),
                    value=_round(value, _CENT),
                )
            )
        for fund in funds:
            # Each tenant pays the fixed contribution once per settlement; the
            # fund is a flat monthly składka (rate effective for the period),
            # not prorated day-by-day.
            lines.append(
                FeeLine(
                    contract_id=contract_id,
                    fee_type="Fund",
                    name=fund.name,
                    usage=Decimal(0),
                    value=_round(fund_rate_for(fund, period_end), _CENT),
                )
            )
    return lines


def _as_utc_midnight(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, time.min), UTC)


@transaction.atomic
def save_settlement(flat: Flat, period_start: date, period_end: date) -> FeeCalculation:
    """Compute and persist a settlement (port of legacy ``saveDueCalc``).

    Creates the ``FeeCalculation`` header, one ``FeeCalculationTenant`` per tenant
    active in the period and a ``FeeCalculationItem`` per fee line.
    """
    lines = calculate_fees(flat, period_start, period_end)
    calc = FeeCalculation.objects.create(
        owner=flat.owner,
        flat=flat,
        stamp=timezone.now(),
        period_start=_as_utc_midnight(period_start),
        period_end=_as_utc_midnight(period_end),
    )

    tenants: dict[int, FeeCalculationTenant] = {}
    for line in lines:
        if line.contract_id not in tenants:
            contract = Contract.all_objects.get(pk=line.contract_id)
            tenants[line.contract_id] = FeeCalculationTenant.objects.create(
                owner=flat.owner,
                flat=flat,
                calculation=calc,
                contract=contract,
                tenant_name=contract.tenant_name,
                contract_number=contract.contract_number,
                email=contract.email,
            )

    FeeCalculationItem.objects.bulk_create(
        FeeCalculationItem(
            owner=flat.owner,
            flat=flat,
            tenant=tenants[line.contract_id],
            fee_type=line.fee_type,
            name=line.name,
            usage=line.usage,
            value=line.value,
        )
        for line in lines
    )
    return calc
