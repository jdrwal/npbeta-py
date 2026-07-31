"""Read-side helpers for dashboards and list views (port of legacy np getters).

Mirrors ``getInvState``, ``getExpIncomeByFlatId`` and ``getOccRoomsByFlatId``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.db.models import F, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Contract, Flat, LedgerEntry, Room

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
        owner=user, contract_start__lte=today, contract_end__gte=today
    )
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
            Contract.objects.filter(room=room, contract_end__gt=today)
            .order_by("-contract_end")
            .first()
        )
        if contract is None:
            result.append(RoomOccupancy(room, "danger", "", None))
            continue
        days_left = (contract.contract_end - today).days if contract.contract_end else 0
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
            owner=user, record_date__gte=start, record_date__lt=end
        ).aggregate(t=Sum("amount_in_taxable"))["t"] or Decimal(0)
        values.append((month, total))

    peak = max((v for _, v in values), default=Decimal(0))
    return [
        MonthPoint(
            label=_PL_MONTH_ABBR[month],
            value=value,
            pct=int(value / peak * 100) if peak > 0 else 0,
        )
        for month, value in values
    ]
