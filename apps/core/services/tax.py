"""Rental-income tax (port of legacy getTaxByMonth / getTaxDeadline / getTaxDue).

Polish lump-sum tax (ryczałt) at a flat 8.5% of taxable income, computed per
calendar month, rounded to whole PLN. Deadline is the 20th of the next month
(31 January for December). Payment dates come from the ``taxdue`` records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Min, Sum
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Flat, LedgerEntry, TaxDue

RATE_LUMP_SUM = Decimal("0.085")


@dataclass
class MonthlyTax:
    year: int
    month: int
    taxable: Decimal
    tax: int
    deadline: date
    paid_date: date | None


def _deadline(year: int, month: int) -> date:
    if month == 12:
        return date(year + 1, 1, 31)
    return date(year, month + 1, 20)


def _to_whole_pln(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def monthly_tax(user: User, year: int, month: int) -> MonthlyTax:
    start = timezone.make_aware(datetime(year, month, 1))
    end = timezone.make_aware(
        datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    )
    taxable = LedgerEntry.objects.filter(
        owner=user, record_date__gte=start, record_date__lt=end
    ).aggregate(total=Sum("amount_in_taxable"))["total"] or Decimal(0)

    paid = TaxDue.objects.filter(owner=user, period=f"{month:02d}/{year}").first()
    paid_date = (
        timezone.localtime(paid.tax_date).date() if paid and paid.tax_date else None
    )

    return MonthlyTax(
        year=year,
        month=month,
        taxable=taxable,
        tax=_to_whole_pln(taxable * RATE_LUMP_SUM),
        deadline=_deadline(year, month),
        paid_date=paid_date,
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
