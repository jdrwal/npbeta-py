"""Per-flat meter matrix (port of legacy ``collectCountersData``).

For every flat, builds a table of reading dates (newest first) with, per meter,
the raw reading, the usage since the previous reading and its cost. Cost is
accumulated day-by-day between consecutive readings so that mid-period price
changes are handled, mirroring the legacy ``calcCounterCostNew``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from apps.accounts.models import User
from apps.core.models import Flat, MeterDefinition, MeterPrice, MeterReading

_CENT = Decimal("0.01")


@dataclass
class MeterCell:
    reading: Decimal | None
    usage: Decimal | None
    cost: Decimal | None
    unit: str
    estimated: bool = False


@dataclass
class MeterRow:
    date: date
    cells: list[MeterCell]
    total: Decimal | None


@dataclass
class FlatMeters:
    flat: Flat
    meters: list[MeterDefinition]
    rows: list[MeterRow]


def _price_lookup(meter: MeterDefinition) -> Callable[[date], Decimal | None]:
    """Return a function giving the unit price effective on/before a day."""
    dated: list[tuple[date, Decimal]] = []
    null_price: Decimal | None = None
    for mp in MeterPrice.objects.filter(meter=meter).order_by("price_date"):
        if mp.price_date is None:
            if null_price is None:
                null_price = mp.price
        else:
            dated.append((mp.price_date, mp.price))

    def price_on(day: date) -> Decimal | None:
        chosen = null_price
        for price_date, price in dated:
            if price_date <= day:
                chosen = price
            else:
                break
        return chosen

    return price_on


def _cost(
    usage: Decimal, prev_date: date, day: date, price_on: Callable[[date], Decimal | None]
) -> Decimal | None:
    per_len = (day - prev_date).days
    if per_len <= 0:
        return None
    total = Decimal(0)
    cursor = prev_date + timedelta(days=1)
    while cursor <= day:
        price = price_on(cursor)
        if price is None:
            return None
        total += (usage / per_len) * price
        cursor += timedelta(days=1)
    return total.quantize(_CENT, rounding=ROUND_HALF_UP)


def counters_matrix(user: User) -> list[FlatMeters]:
    """Build the reading/usage/cost matrix for every flat that has meters."""
    result: list[FlatMeters] = []
    for flat in Flat.objects.filter(owner=user).order_by("id"):
        meters = list(
            MeterDefinition.objects.filter(owner=user, flat=flat).order_by("id")
        )
        if not meters:
            continue

        price_fns = {m.id: _price_lookup(m) for m in meters}
        values: dict[int, dict[date, Decimal]] = {m.id: {} for m in meters}
        estimated_map: dict[int, dict[date, bool]] = {m.id: {} for m in meters}
        dates_set: set[date] = set()
        for r in MeterReading.objects.filter(flat=flat, meter__in=meters):
            values[r.meter_id][r.read_date] = r.value
            estimated_map[r.meter_id][r.read_date] = r.is_estimated
            dates_set.add(r.read_date)

        dates = sorted(dates_set, reverse=True)
        rows: list[MeterRow] = []
        for i, day in enumerate(dates):
            prev_date = dates[i + 1] if i + 1 < len(dates) else None
            cells: list[MeterCell] = []
            total = Decimal(0)
            has_cost = False
            for m in meters:
                reading = values[m.id].get(day)
                usage: Decimal | None = None
                cost: Decimal | None = None
                if prev_date is not None and reading is not None:
                    prev_reading = values[m.id].get(prev_date)
                    if prev_reading is not None:
                        usage = reading - prev_reading
                        cost = _cost(usage, prev_date, day, price_fns[m.id])
                        if cost is not None:
                            total += cost
                            has_cost = True
                cells.append(
                    MeterCell(
                        reading=reading,
                        usage=usage,
                        cost=cost,
                        unit=m.unit,
                        estimated=estimated_map[m.id].get(day, False),
                    )
                )
            rows.append(
                MeterRow(date=day, cells=cells, total=total if has_cost else None)
            )
        result.append(FlatMeters(flat=flat, meters=meters, rows=rows))
    return result
