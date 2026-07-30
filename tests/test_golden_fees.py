"""Golden test: the ported fee engine reproduces a real legacy settlement.

Uses an anonymized fixture exported from one stable (non-drifted) legacy
calculation. The recomputed per-tenant fee lines must match the stored legacy
values to the cent. See apps/core/services/fees.py and the note on historical
data drift for why only stable calculations are frozen as golden cases.
"""

from collections import defaultdict
from decimal import Decimal

import pytest
from django.core.management import call_command

from apps.core.models import FeeCalculation, FeeCalculationItem
from apps.core.services.fees import calculate_fees

TOLERANCE = Decimal("0.01")

GOLDEN_FIXTURES = [
    "tests/fixtures/golden_fees.json",
    "tests/fixtures/golden_fees_flat2.json",
]


@pytest.mark.django_db
@pytest.mark.parametrize("fixture", GOLDEN_FIXTURES)
def test_golden_fee_settlement_matches_legacy(fixture: str) -> None:
    call_command("loaddata", fixture, verbosity=0)
    calc = FeeCalculation.objects.get()

    expected: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    for item in FeeCalculationItem.objects.filter(tenant__calculation=calc):
        expected[(item.fee_type, item.name)].append(item.value)

    recomputed: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    for line in calculate_fees(
        calc.flat, calc.period_start.date(), calc.period_end.date()
    ):
        recomputed[(line.fee_type, line.name)].append(line.value)

    assert set(expected) == set(recomputed)

    for key, exp_values in expected.items():
        exp_sorted = sorted(exp_values)
        rec_sorted = sorted(recomputed[key])
        assert len(exp_sorted) == len(rec_sorted), key
        for exp_v, rec_v in zip(exp_sorted, rec_sorted, strict=True):
            assert abs(exp_v - rec_v) <= TOLERANCE, f"{key}: {exp_v} vs {rec_v}"

    expected_total = Decimal(0)
    for values in expected.values():
        for value in values:
            expected_total += value
    recomputed_total = Decimal(0)
    for values in recomputed.values():
        for value in values:
            recomputed_total += value
    assert abs(expected_total - recomputed_total) <= TOLERANCE
