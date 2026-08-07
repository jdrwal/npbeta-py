"""Read-side helpers for dashboards and list views (port of legacy np getters).

Mirrors ``getInvState``, ``getExpIncomeByFlatId`` and ``getOccRoomsByFlatId``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Contract, FeeCalculation, Flat, LedgerEntry, Room

_PL_MONTH_ABBR = [
    "",
    "sty",
    "lut",
    "mar",
    "kwi",
    "maj",
    "cze",
    "lip",
    "sie",
    "wrz",
    "paź",
    "lis",
    "gru",
]


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    return date(year, month % 12 + 1, min(d.day, 28))


@dataclass
class InventoryState:
    flats: int
    rooms: int
    active_contracts: int
    contracts_ending_soon: int


def inventory_state(user: User) -> InventoryState:
    """Counts for the dashboard cards (active = today within contract dates)."""
    today = timezone.now().date()
    soon = _add_months(today, 2)
    active = Contract.objects.filter(
        owner=user, contract_start__lte=today
    ).filter(Q(contract_end__gte=today) | Q(contract_end__isnull=True))
    return InventoryState(
        flats=Flat.objects.filter(owner=user).count(),
        rooms=Room.objects.filter(owner=user).count(),
        active_contracts=active.count(),
        contracts_ending_soon=active.filter(contract_end__lte=soon).count(),
    )


def expected_income(flat: Flat) -> Decimal:
    """Expected monthly income for a flat: sum(room.fee * room.beds)."""
    total = Room.objects.filter(flat=flat).aggregate(
        total=Sum(F("fee") * F("beds"))
    )["total"]
    return total or Decimal(0)


@dataclass
class RoomOccupancy:
    room: Room
    status: str  # "success" | "warning" | "danger"
    tenant_name: str
    contract_end: date | None


def occupancy(flat: Flat) -> list[RoomOccupancy]:
    """Per-room occupancy status for a flat.

    danger  = no active contract; warning = ends within 60 days;
    success = ends in more than 60 days.
    """
    today = timezone.now().date()
    result: list[RoomOccupancy] = []
    for room in Room.objects.filter(flat=flat).order_by("room_no"):
        contract = (
            Contract.objects.filter(room=room, contract_start__lte=today)
            .filter(Q(contract_end__gt=today) | Q(contract_end__isnull=True))
            .order_by(F("contract_end").desc(nulls_first=True))
            .first()
        )
        if contract is None:
            result.append(RoomOccupancy(room, "danger", "", None))
            continue
        if contract.contract_end is None:
            status = "success"  # open-ended lease, no end in sight
        else:
            days_left = (contract.contract_end - today).days
            status = "success" if days_left > 60 else "warning"
        result.append(
            RoomOccupancy(room, status, contract.tenant_name, contract.contract_end)
        )
    return result


@dataclass
class MonthPoint:
    label: str
    value: Decimal
    pct: int


def monthly_income_series(user: User, months: int = 6) -> list[MonthPoint]:
    """Taxable income per month for the last ``months`` months (oldest first)."""
    today = timezone.now().date()
    periods: list[tuple[int, int]] = []
    for offset in range(months - 1, -1, -1):
        month = today.month - offset
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        periods.append((year, month))

    values: list[tuple[int, Decimal]] = []
    for year, month in periods:
        start = timezone.make_aware(datetime(year, month, 1))
        end = timezone.make_aware(
            datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
        )
        total = LedgerEntry.objects.filter(
            owner=user,
            record_date__gte=start,
            record_date__lt=end,
            kind=LedgerEntry.Kind.RENT,
        ).aggregate(t=Sum("amount_in_taxable"))["t"] or Decimal(0)
        values.append((month, total))

    peak = max((v for _, v in values), default=Decimal(0))
    return [
        MonthPoint(
            label=_PL_MONTH_ABBR[month],
            value=value,
            # Cap at 90% so the amount label above the tallest bar stays visible.
            pct=int(value / peak * 90) if peak > 0 else 0,
        )
        for month, value in values
    ]


@dataclass
class FeeArrear:
    """One computed-but-not-confirmed utility settlement owed by a tenant."""

    flat: Flat
    flat_id: int
    tenant_name: str
    period_label: str  # the settlement period the fee is for (MM/YYYY)
    amount: Decimal
    calc_id: int
    bill_year: int  # month where the fee is confirmed (Ewidencja link)
    bill_month: int
    overdue: bool = False  # billing month already passed → a real debt


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def unconfirmed_fees(user: User) -> list[FeeArrear]:
    """Saved settlements (Pozostałe opłaty) not yet confirmed as a fee payment.

    A settlement tenant is confirmed once a ``fee`` ledger entry links back to
    it (``settlement_tenant``). Anything still unconfirmed is surfaced as a
    dashboard notification.
    """
    confirmed_tenant_ids = set(
        LedgerEntry.objects.filter(
            owner=user,
            kind=LedgerEntry.Kind.FEE,
            settlement_tenant__isnull=False,
        ).values_list("settlement_tenant_id", flat=True)
    )

    items: list[FeeArrear] = []
    calcs = (
        FeeCalculation.objects.filter(owner=user)
        .select_related("flat")
        .prefetch_related("tenants__items")
    )
    today = timezone.now().date()
    cur = (today.year, today.month)
    for calc in calcs:
        midpoint = calc.period_start + (calc.period_end - calc.period_start) / 2
        p_year, p_month = midpoint.year, midpoint.month
        bill_year, bill_month = _next_month(p_year, p_month)
        for tenant in calc.tenants.all():
            total = sum((it.value for it in tenant.items.all()), Decimal(0))
            if total <= 0:
                continue
            if tenant.pk in confirmed_tenant_ids:
                continue
            items.append(
                FeeArrear(
                    flat=calc.flat,
                    flat_id=calc.flat_id,
                    tenant_name=tenant.tenant_name,
                    period_label=f"{p_month:02d}/{p_year}",
                    amount=total,
                    calc_id=calc.pk,
                    bill_year=bill_year,
                    bill_month=bill_month,
                    overdue=(bill_year, bill_month) < cur,
                )
            )
    items.sort(key=lambda x: (x.period_label, x.tenant_name), reverse=True)
    return items
