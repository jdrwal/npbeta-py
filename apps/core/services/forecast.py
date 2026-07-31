"""Forecast rental income (port of legacy getForecastIncome / getRatio).

For a given month, each overlapping contract contributes ``price * ratio`` where
``ratio`` is the fraction of the month the contract is active (inclusive of both
boundary days), rounded to the grosz.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Contract, Flat, LedgerEntry

_CENT = Decimal("0.01")


def rent_due_status(
    first: date, last: date, payment_day: int | None, today: date
) -> str | None:
    """Rent due-date status (port of verifyForeDeadline): GRN/AMB/RED/EXC.

    GRN = before the deadline, AMB = due date, RED = past deadline but still in
    month, EXC = the whole month has passed (a real debt).
    """
    if not payment_day:
        return None
    dline = first + timedelta(days=payment_day)
    if today < dline - timedelta(days=1):
        return "GRN"
    if today < dline:
        return "AMB"
    if today <= last:
        return "RED"
    return "EXC"


@dataclass
class RentArrear:
    contract: Contract
    period: str
    amount: Decimal
    status: str


def rent_arrears(user: User, months: int = 12) -> list[RentArrear]:
    """Overdue rent (RED/EXC) with no recorded payment over the last ``months``."""
    today = timezone.now().date()
    periods: list[tuple[int, int]] = []
    for offset in range(months - 1, -1, -1):
        m = today.month - offset
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        periods.append((y, m))

    window_start = date(periods[0][0], periods[0][1], 1)
    paid: set[tuple[int, int, int]] = set()
    for cid, bp in LedgerEntry.objects.filter(
        owner=user,
        contract__isnull=False,
        billing_period__gte=window_start,
        kind=LedgerEntry.Kind.RENT,
    ).values_list("contract_id", "billing_period"):
        if bp:
            paid.add((cid, bp.year, bp.month))

    contracts = list(
        Contract.objects.filter(owner=user).select_related("flat", "room")
    )
    items: list[RentArrear] = []
    for year, month in periods:
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
        for ct in contracts:
            if ct.price is None:
                continue
            if ct.contract_start and ct.contract_start > last:
                continue
            if ct.contract_end and ct.contract_end < first:
                continue
            if (ct.pk, year, month) in paid:
                continue
            status = rent_due_status(first, last, ct.payment_day, today)
            if status not in ("RED", "EXC"):
                continue
            amount = (ct.price * contract_ratio(ct, year, month)).quantize(
                _CENT, rounding=ROUND_HALF_UP
            )
            items.append(RentArrear(ct, f"{month:02d}/{year}", amount, status))
    items.sort(key=lambda x: x.period, reverse=True)
    return items


def contract_ratio(contract: Contract, year: int, month: int) -> Decimal:
    """Fraction of the month the contract is active (0..1)."""
    if contract.contract_start is None or contract.contract_end is None:
        return Decimal(0)
    days_in_month = calendar.monthrange(year, month)[1]
    first = date(year, month, 1)
    last = date(year, month, days_in_month)
    active_start = max(contract.contract_start, first)
    active_end = min(contract.contract_end, last)
    if active_end < active_start:
        return Decimal(0)
    active_days = (active_end - active_start).days + 1
    return Decimal(active_days) / Decimal(days_in_month)


def forecast_income(flat: Flat, year: int, month: int) -> Decimal:
    """Expected rent for a flat in a month, prorated across active contracts."""
    days_in_month = calendar.monthrange(year, month)[1]
    first = date(year, month, 1)
    last = date(year, month, days_in_month)
    total = Decimal(0)
    contracts = Contract.all_objects.filter(
        flat=flat, contract_start__lte=last, contract_end__gte=first
    )
    for contract in contracts:
        if contract.price is None:
            continue
        share = contract.price * contract_ratio(contract, year, month)
        total += share.quantize(_CENT, rounding=ROUND_HALF_UP)
    return total


def forecast_income_total(user: User, year: int, month: int) -> Decimal:
    """Expected rent across all of the owner's flats for a month."""
    return sum(
        (forecast_income(flat, year, month) for flat in Flat.objects.filter(owner=user)),
        Decimal(0),
    )
