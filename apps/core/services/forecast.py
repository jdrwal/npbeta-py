"""Forecast rental income (port of legacy getForecastIncome / getRatio).

For a given month, each overlapping contract contributes ``price * ratio`` where
``ratio`` is the fraction of the month the contract is active (inclusive of both
boundary days), rounded to the grosz.
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from apps.accounts.models import User
from apps.core.models import Contract, Flat

_CENT = Decimal("0.01")


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
