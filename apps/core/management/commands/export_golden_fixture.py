"""Dev tool: export one stable calculation as an anonymized golden-test fixture.

Collects the full input slice a fee settlement depends on (flat, rooms,
contracts, meters + readings + prices, admin fees + prices) plus the saved
calculation and its expected fee lines, anonymizes tenant PII, and writes a
Django fixture consumable by tests. Readings are windowed around the period to
keep the fixture small.

    python manage.py export_golden_fixture 302 --output tests/fixtures/golden_fees.json
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core import serializers
from django.core.management.base import BaseCommand, CommandError

from apps.core.models import (
    AdminFee,
    AdminFeePrice,
    Contract,
    FeeCalculation,
    FeeCalculationItem,
    FeeCalculationTenant,
    MeterDefinition,
    MeterPrice,
    MeterReading,
    Room,
)

WINDOW = timedelta(days=90)


class Command(BaseCommand):
    help = "Export a calculation and its inputs as an anonymized golden fixture."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("calc_id", type=int)
        parser.add_argument("--output", default="tests/fixtures/golden_fees.json")

    def handle(self, *args: Any, **options: Any) -> None:
        calc = FeeCalculation.objects.filter(pk=options["calc_id"]).first()
        if calc is None:
            raise CommandError(f"FeeCalculation {options['calc_id']} not found")

        flat = calc.flat
        owner = flat.owner
        lo = calc.period_start.date() - WINDOW
        hi = calc.period_end.date() + WINDOW

        meters = list(MeterDefinition.objects.filter(flat=flat))
        admin_fees = list(AdminFee.objects.filter(flat=flat))

        # Anonymize the owner (in memory only — never saved).
        owner.username = "owner"
        owner.email = ""
        owner.first_name = ""
        owner.last_name = ""
        owner.password = "!"

        contracts = list(
            Contract.all_objects.filter(
                flat=flat,
                contract_end__gte=calc.period_start.date(),
                contract_start__lte=calc.period_end.date(),
            )
        )
        for c in contracts:
            c.tenant_name = f"Tenant {c.id}"
            c.email = ""
            c.phone = ""

        tenants = list(FeeCalculationTenant.objects.filter(calculation=calc))
        for t in tenants:
            t.tenant_name = f"Tenant {t.id}"
            t.email = ""
            t.flat = flat

        objects: list[Any] = [owner, flat]
        objects += list(Room.all_objects.filter(flat=flat))
        objects += contracts
        objects += meters
        readings = list(
            MeterReading.objects.filter(
                meter__in=meters, read_date__gte=lo, read_date__lte=hi
            )
        )
        meter_prices = list(MeterPrice.objects.filter(meter__in=meters))
        admin_prices = list(AdminFeePrice.objects.filter(admin_fee__in=admin_fees))
        # Legacy `fid` columns are occasionally inconsistent; the engine keys off
        # meter/admin_fee, so normalize the redundant flat FK to satisfy loaddata.
        for obj in (*readings, *meter_prices, *admin_prices):
            obj.flat = flat  # type: ignore[attr-defined]
        objects += readings
        objects += meter_prices
        objects += admin_fees
        objects += admin_prices
        objects += [calc]
        objects += tenants
        items = list(FeeCalculationItem.objects.filter(tenant__in=tenants))
        for item in items:
            item.flat = flat
        objects += items

        data = serializers.serialize("json", objects, indent=1)
        with open(options["output"], "w") as fh:
            fh.write(data)

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {len(objects)} objects for calc {calc.id} to {options['output']}"
            )
        )
