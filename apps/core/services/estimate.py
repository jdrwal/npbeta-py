"""Estimate a meter reading from past usage.

When a meter cannot be read on time, its next reading can be estimated by
projecting the average daily usage of the last ``months`` months forward from
the most recent real reading to the target date. Meter values are cumulative,
so usage over a window is ``value_end - value_start`` and the daily rate is that
divided by the number of days between the two readings.
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from apps.core.models import Flat, MeterDefinition, MeterReading

_STEP = Decimal("0.001")


def _months_before(day: date, months: int) -> date:
    """Return the date ``months`` calendar months before ``day``."""
    total = day.year * 12 + (day.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def estimate_reading(
    meter: MeterDefinition, target_date: date, months: int
) -> Decimal | None:
    """Estimate ``meter``'s reading on ``target_date`` from the last ``months``.

    Returns ``None`` when there is not enough history to project a value.
    """
    readings = list(
        MeterReading.objects.filter(meter=meter).order_by("read_date", "id")
    )
    if len(readings) < 2:
        return None

    last = readings[-1]
    window_start = _months_before(target_date, months)

    # Baseline = the most recent reading on/before the window start, so the
    # window spans roughly the requested number of months. Fall back to the
    # earliest reading when every reading falls inside the window.
    baseline = None
    for r in readings:
        if r.read_date <= window_start:
            baseline = r
        else:
            break
    if baseline is None or baseline.read_date >= last.read_date:
        baseline = readings[0]

    span_days = (last.read_date - baseline.read_date).days
    if span_days <= 0:
        return None

    avg_daily = (last.value - baseline.value) / span_days
    if avg_daily < 0:  # meter reset / bad data — never estimate a lower reading
        avg_daily = Decimal(0)

    forward = (target_date - last.read_date).days
    estimate = last.value + avg_daily * forward if forward > 0 else last.value
    return estimate.quantize(_STEP, rounding=ROUND_HALF_UP)


def estimate_flat_readings(
    flat: Flat, target_date: date, months: int
) -> dict[int, Decimal]:
    """Estimate every meter of ``flat`` for ``target_date`` (skips those without
    enough history)."""
    estimates: dict[int, Decimal] = {}
    for meter in MeterDefinition.objects.filter(flat=flat).order_by("id"):
        value = estimate_reading(meter, target_date, months)
        if value is not None:
            estimates[meter.id] = value
    return estimates
