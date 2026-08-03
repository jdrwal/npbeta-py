"""Balance logic for contribution funds (see ``core.models.Fund``).

A fund is a per-flat pool of money: tenants pay a fixed monthly contribution
into it (accrued automatically) and the landlord spends from it now and then.
The balance is simply everything paid in minus everything paid out; it is kept
separate from rental income (no effect on income/tax/ledger).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.core.models import Fund

_CENT = Decimal("0.01")


def _months_inclusive(start: date, end: date) -> int:
    """Whole calendar months from ``start`` to ``end`` inclusive (0 if before)."""
    if end < start:
        return 0
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def accrued_months(fund: Fund, as_of: date) -> int:
    """Number of months the fixed contribution has accrued up to ``as_of``."""
    end = as_of
    if fund.end_date and fund.end_date < end:
        end = fund.end_date
    return _months_inclusive(fund.start_date, end)


def accrued_amount(fund: Fund, as_of: date) -> Decimal:
    """Auto-accrued contributions from the start month up to ``as_of``."""
    return (fund.monthly_amount * accrued_months(fund, as_of)).quantize(_CENT)


@dataclass
class FundBalance:
    fund: Fund
    months: int
    accrued: Decimal  # sum of the fixed monthly contributions
    manual: Decimal  # ad-hoc contributions (top-ups / corrections)
    contributed: Decimal  # accrued + manual (total paid in)
    spent: Decimal  # total paid out
    balance: Decimal  # contributed - spent
    is_active: bool


def fund_balance(fund: Fund, as_of: date | None = None) -> FundBalance:
    """Compute a fund's balance as of ``as_of`` (default: today)."""
    if as_of is None:
        as_of = timezone.localdate()
    months = accrued_months(fund, as_of)
    accrued = (fund.monthly_amount * months).quantize(_CENT)
    manual = fund.contributions.filter(contributed_on__lte=as_of).aggregate(
        s=Sum("amount")
    )["s"] or Decimal("0")
    spent = fund.expenses.filter(spent_on__lte=as_of).aggregate(s=Sum("amount"))[
        "s"
    ] or Decimal("0")
    contributed = (accrued + manual).quantize(_CENT)
    balance = (contributed - spent).quantize(_CENT)
    is_active = fund.end_date is None or fund.end_date >= as_of
    return FundBalance(
        fund=fund,
        months=months,
        accrued=accrued,
        manual=manual.quantize(_CENT),
        contributed=contributed,
        spent=spent.quantize(_CENT),
        balance=balance,
        is_active=is_active,
    )
