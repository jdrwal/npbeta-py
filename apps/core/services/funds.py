"""Balance logic for contribution funds (see ``core.models.Fund``).

A fund is a per-flat pool of money. Each tenant pays a fixed contribution
(``monthly_amount``) on top of their utility bills; the contribution lands in
the pool at the moment the tenant's bills are confirmed as paid. In this app a
confirmed bills payment is a ``fee`` ledger entry (``LedgerEntry.Kind.FEE``),
so the collected contributions are simply ``monthly_amount`` times the number
of confirmed fee payments for the flat within the fund's active window.

The landlord also records ad-hoc contributions and the expenses paid out of the
pool. The balance is everything paid in minus everything paid out; it is kept
separate from rental income (no effect on income/tax/ledger).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.core.models import Fund, LedgerEntry

_CENT = Decimal("0.01")


def confirmed_payment_count(fund: Fund, as_of: date) -> int:
    """Number of confirmed bill payments feeding the fund up to ``as_of``.

    Each ``fee`` ledger entry for the fund's flat represents one tenant whose
    bills (and therefore the fund contribution) were confirmed as paid. Only
    payments whose billing month falls inside the fund's active window
    (``start_date``..``end_date``/``as_of``) are counted.
    """
    start_month = fund.start_date.replace(day=1)
    end_cap = as_of
    if fund.end_date and fund.end_date < end_cap:
        end_cap = fund.end_date
    if end_cap < start_month:
        return 0
    return LedgerEntry.objects.filter(
        flat=fund.flat,
        kind=LedgerEntry.Kind.FEE,
        billing_period__isnull=False,
        billing_period__gte=start_month,
        billing_period__lte=end_cap,
    ).count()


@dataclass
class FundBalance:
    fund: Fund
    count: int  # number of confirmed bill payments (each adds one contribution)
    accrued: Decimal  # count × monthly_amount (contributions collected with bills)
    manual: Decimal  # ad-hoc contributions (top-ups / corrections)
    contributed: Decimal  # accrued + manual (total paid in)
    spent: Decimal  # total paid out
    balance: Decimal  # contributed - spent
    is_active: bool


def fund_balance(fund: Fund, as_of: date | None = None) -> FundBalance:
    """Compute a fund's balance as of ``as_of`` (default: today)."""
    if as_of is None:
        as_of = timezone.localdate()
    count = confirmed_payment_count(fund, as_of)
    accrued = (fund.monthly_amount * count).quantize(_CENT)
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
        count=count,
        accrued=accrued,
        manual=manual.quantize(_CENT),
        contributed=contributed,
        spent=spent.quantize(_CENT),
        balance=balance,
        is_active=is_active,
    )
