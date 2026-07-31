"""Rental-income tax (port of legacy getTaxByMonth / getTaxDeadline / getTaxDue).

Polish private rental income is taxed only by lump-sum (ryczałt) since 2023:
8.5% on annual income up to 100 000 PLN and 12.5% on the surplus above it. The
threshold is cumulative per calendar year, so a month's tax depends on the
year-to-date taxable income. Amounts are rounded to whole PLN; the deadline is
the 20th of the next month (31 January for December). Payment dates come from
the ``taxdue`` records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Min, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Flat, LedgerEntry, TaxDue

RATE_LOW = Decimal("0.085")  # up to the annual threshold
RATE_HIGH = Decimal("0.125")  # on the surplus above the threshold
ANNUAL_THRESHOLD = Decimal("100000")


@dataclass
class MonthlyTax:
    year: int
    month: int
    taxable: Decimal
    tax: int
    deadline: date
    paid_date: date | None
    paid_amount: int | None = None
    paid_pct: int | None = None
    paid_pk: int | None = None


def _deadline(year: int, month: int) -> date:
    if month == 12:
        return date(year + 1, 1, 31)
    return date(year, month + 1, 20)


def _to_whole_pln(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _sum_taxable(user: User, start: datetime, end: datetime) -> Decimal:
    return LedgerEntry.objects.filter(
        owner=user,
        record_date__gte=start,
        record_date__lt=end,
        kind=LedgerEntry.Kind.RENT,
    ).aggregate(total=Sum("amount_in_taxable"))["total"] or Decimal(0)


def monthly_tax(user: User, year: int, month: int) -> MonthlyTax:
    year_start = timezone.make_aware(datetime(year, 1, 1))
    month_start = timezone.make_aware(datetime(year, month, 1))
    month_end = timezone.make_aware(
        datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    )

    ytd_before = _sum_taxable(user, year_start, month_start)
    month_taxable = _sum_taxable(user, month_start, month_end)

    # Split this month's income across the 8.5% / 12.5% brackets by YTD position.
    remaining_low = max(Decimal(0), ANNUAL_THRESHOLD - ytd_before)
    low_part = min(month_taxable, remaining_low)
    high_part = month_taxable - low_part
    tax = _to_whole_pln(low_part * RATE_LOW + high_part * RATE_HIGH)

    paid = TaxDue.objects.filter(owner=user, period=f"{month:02d}/{year}").first()
    paid_date = (
        timezone.localtime(paid.tax_date).date() if paid and paid.tax_date else None
    )
    paid_amount = paid.tax_amount if paid else None
    if paid is None:
        paid_pct = None
    elif tax > 0:
        paid_pct = round(paid.tax_amount / tax * 100)
    else:
        paid_pct = 100

    return MonthlyTax(
        year=year,
        month=month,
        taxable=month_taxable,
        tax=tax,
        deadline=_deadline(year, month),
        paid_date=paid_date,
        paid_amount=paid_amount,
        paid_pct=paid_pct,
        paid_pk=paid.pk if paid else None,
    )


def tax_table(user: User) -> dict[int, list[MonthlyTax]]:
    """Completed months from the first rental start to today (newest first)."""
    first = Flat.objects.filter(owner=user, rental_start__isnull=False).aggregate(
        earliest=Min("rental_start")
    )["earliest"]
    if first is None:
        return {}

    today = timezone.now().date()
    table: dict[int, list[MonthlyTax]] = {}
    for year in range(today.year, first.year - 1, -1):
        months = [
            monthly_tax(user, year, month)
            for month in range(12, 0, -1)
            if not (year == today.year and month >= today.month)
        ]
        if months:
            table[year] = months
    return table


def tax_for_year(user: User, year: int) -> int:
    """Total lump-sum tax owed for the completed months of a year."""
    return sum(m.tax for m in tax_table(user).get(year, []))
