"""Dev/ops tool: compare the ported fee engine against the legacy saved results.

For every imported FeeCalculation it recomputes the settlement with
``apps.core.services.fees.calculate_fees`` and compares the resulting fee lines
to the stored ``FeeCalculationItem`` values (legacy ``duefees``).

Tenant identity is NOT used for matching: legacy ``contract_number`` is not
unique (renewals reuse it), so within each calculation we compare the multiset
of values grouped by ``(fee_type, name)``. This validates the numbers without
depending on fuzzy tenant identity.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand

from apps.core.models import FeeCalculation, FeeCalculationItem
from apps.core.services.fees import calculate_fees

TOLERANCE = Decimal("0.01")


class Command(BaseCommand):
    help = "Compare the ported fee engine to the legacy stored calculations."

    def handle(self, *args: Any, **options: Any) -> None:
        total = matched = mismatch = count_mismatch = 0
        max_diff = Decimal(0)
        examples: list[str] = []

        for calc in FeeCalculation.objects.all():
            expected: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
            for item in FeeCalculationItem.objects.filter(tenant__calculation=calc):
                expected[(item.fee_type, item.name)].append(item.value)

            recomputed: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
            for line in calculate_fees(
                calc.flat, calc.period_start.date(), calc.period_end.date()
            ):
                recomputed[(line.fee_type, line.name)].append(line.value)

            for key, exp_values in expected.items():
                exp_sorted = sorted(exp_values)
                rec_sorted = sorted(recomputed.get(key, []))
                total += len(exp_sorted)
                if len(exp_sorted) != len(rec_sorted):
                    count_mismatch += 1
                    if len(examples) < 12:
                        examples.append(
                            f"calc {calc.id} {key}: tenant count "
                            f"{len(exp_sorted)} vs {len(rec_sorted)}"
                        )
                    continue
                for exp_v, rec_v in zip(exp_sorted, rec_sorted, strict=True):
                    diff = abs(exp_v - rec_v)
                    max_diff = max(max_diff, diff)
                    if diff <= TOLERANCE:
                        matched += 1
                    else:
                        mismatch += 1
                        if len(examples) < 12:
                            examples.append(
                                f"calc {calc.id} {key}: {exp_v} vs {rec_v} (Δ{diff})"
                            )

        self.stdout.write(f"calculations   : {FeeCalculation.objects.count()}")
        self.stdout.write(f"lines compared : {total}")
        self.stdout.write(f"value matched  : {matched} (±{TOLERANCE})")
        self.stdout.write(f"value mismatch : {mismatch}")
        self.stdout.write(f"count mismatch : {count_mismatch} groups (tenant-count differs)")
        self.stdout.write(f"max value diff : {max_diff}")
        if examples:
            self.stdout.write("examples:")
            for example in examples:
                self.stdout.write(f"  {example}")
