"""Mortgage schedule (port of legacy getMortRecords).

A flat carries a mortgage when it has a ``mortgage_start`` and a monthly amount.
For each month the estimated installment is the fixed ``mortgage_amount`` with an
``mortgage_interest`` cost component. An estimate is only relevant for months at
or after the mortgage start; the current month is flagged if a real mortgage
ledger entry already exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Flat, LedgerEntry

_CENT = Decimal("0.01")


def _round(value: Decimal | None) -> Decimal:
    return (value or Decimal(0)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _add_months(d: date, months: int) -> date:
    total = d.month - 1 + months
    return date(d.year + total // 12, total % 12 + 1, 1)


@dataclass
class MortgageInstallment:
    flat: Flat
    year: int
    month: int
    amount: Decimal
    interest: Decimal
    recorded: bool


def is_mortgaged(flat: Flat) -> bool:
    return flat.mortgage_start is not None and flat.mortgage_amount is not None


def _recorded_in_month(flat: Flat, year: int, month: int) -> bool:
    start = timezone.make_aware(datetime(year, month, 1))
    end = timezone.make_aware(
        datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    )
    return LedgerEntry.objects.filter(
        flat=flat, is_mortgage=True, record_date__gte=start, record_date__lt=end
    ).exists()


def mortgage_schedule(user: User, months: int = 12) -> list[MortgageInstallment]:
    """Estimated installments for the next ``months`` months, mortgaged flats."""
    today = timezone.now().date()
    schedule: list[MortgageInstallment] = []
    for flat in Flat.objects.filter(owner=user):
        if not is_mortgaged(flat):
            continue
        for offset in range(months):
            when = _add_months(today.replace(day=1), offset)
            if flat.mortgage_start is not None and date(
                when.year, when.month, 1
            ) < flat.mortgage_start.date().replace(day=1):
                continue
            schedule.append(
                MortgageInstallment(
                    flat=flat,
                    year=when.year,
                    month=when.month,
                    amount=_round(flat.mortgage_amount),
                    interest=_round(flat.mortgage_interest),
                    recorded=_recorded_in_month(flat, when.year, when.month),
                )
            )
    return schedule
