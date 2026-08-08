"""DRY-RUN by default: backfill a 'Fund' line item into every settlement tenant
in each fund's window that lacks one, with the period rate (15/25 via FundRate).
Set FUNDLINE_APPLY=1 to write. Balance is unaffected (accrual is count-based)."""

import os
from decimal import Decimal

from django.db import transaction

from apps.core.models import FeeCalculationItem, Fund

APPLY = os.environ.get("FUNDLINE_APPLY") == "1"


def _next_month(d):
    return d.replace(year=d.year + 1, month=1) if d.month == 12 else d.replace(
        month=d.month + 1
    )


def _rate_for(fund, billing_month):
    r = fund.rates.filter(rate_date__lte=billing_month).order_by("-rate_date").first()
    return r.amount if r else fund.monthly_amount


for fund in Fund.objects.filter(owner_id=1).order_by("id"):
    flat = fund.flat
    start = fund.start_date
    planned = []  # (tenant_id, value)
    by_rate: dict = {}
    tenants = (
        __import__("apps.core.models", fromlist=["FeeCalculationTenant"])
        .FeeCalculationTenant.objects.filter(flat=flat)
        .select_related("calculation")
    )
    for t in tenants:
        c = t.calculation
        mid = (c.period_start + (c.period_end - c.period_start) / 2).date()
        if mid < start:
            continue
        if FeeCalculationItem.objects.filter(tenant=t, fee_type="Fund").exists():
            continue
        billing = _next_month(mid.replace(day=1))
        rate = _rate_for(fund, billing)
        planned.append((t, rate))
        by_rate[rate] = by_rate.get(rate, 0) + 1

    total = sum((r for _, r in planned), Decimal("0"))
    print("=" * 56)
    print(f"FUND id={fund.id} @ {flat}")
    print(f"  Fund lines to add: {len(planned)}  (by rate: "
          + ", ".join(f"{k}×{v}" for k, v in sorted(by_rate.items())) + ")")
    print(f"  sum of added Fund line values: {total} zł")

    if APPLY and planned:
        with transaction.atomic():
            FeeCalculationItem.objects.bulk_create(
                FeeCalculationItem(
                    owner=fund.owner, flat=flat, tenant=t,
                    fee_type="Fund", name=fund.name,
                    usage=Decimal("0"), value=rate,
                )
                for t, rate in planned
            )
        print(f"  APPLIED: {len(planned)} Fund line items created.")

print("=" * 56)
print("MODE:", "APPLIED" if APPLY else "DRY-RUN (nothing written)")
