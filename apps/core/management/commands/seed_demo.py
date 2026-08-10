"""Dev-only tool: seed a fictional landlord with properties, tenancy history and
four years of rent payments, plus one tenant account linked to an active lease.

The command is idempotent and strictly scoped to two demo accounts: it deletes
the demo landlord and demo tenant (by email) and everything they own, then
recreates a fresh dataset. It NEVER touches any other account's data.

    python manage.py seed_demo

Refuses to run unless DEBUG is on (safety), overridable with --force.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import (
    AdminFee,
    AdminFeePrice,
    Contract,
    Flat,
    Fund,
    LedgerEntry,
    MeterDefinition,
    MeterPrice,
    MeterReading,
    Room,
    TaxDue,
)
from apps.core.services import tax as tax_service

LANDLORD_EMAIL = "demo.wynajmujacy@example.com"
TENANT_EMAIL = "anna.kowalska@example.com"
DEMO_PASSWORD = "Demo1234!"  # noqa: S105 - fictional dev-only credentials

# How much to leave unsettled, so the data looks realistic rather than perfect.
N_MISSING_PAYMENTS = 5  # rent payments dropped across all contracts (arrears)
N_UNPAID_TAX = 3  # most-recent monthly taxes left unpaid

# Gendered name pools so first name and surname agree (Polish surnames inflect
# by gender: -ski/-ska, -cki/-cka). Neutral surnames (Nowak, Wójcik...) are
# listed in both. Used to build realistic tenant names.
_FEMALE_FIRST = [
    "Anna", "Katarzyna", "Agnieszka", "Magdalena", "Joanna", "Ewa", "Monika",
]
_MALE_FIRST = [
    "Piotr", "Marcin", "Tomasz", "Paweł", "Michał", "Grzegorz", "Krzysztof",
]
_FEMALE_LAST = [
    "Kowalska", "Nowak", "Wiśniewska", "Wójcik", "Kowalczyk", "Kamińska",
    "Lewandowska", "Zielińska", "Szymańska", "Woźniak", "Dąbrowska", "Kozłowska",
]
_MALE_LAST = [
    "Kowalski", "Nowak", "Wiśniewski", "Wójcik", "Kowalczyk", "Kamiński",
    "Lewandowski", "Zieliński", "Szymański", "Woźniak", "Dąbrowski", "Kozłowski",
]

# Fold Polish diacritics to ASCII for e-mail local parts.
_PL_ASCII = str.maketrans(
    {
        "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o",
        "ś": "s", "ż": "z", "ź": "z",
    }
)

# Flats to create: (city, street, building_no, flat_no, size m², code, rooms).
# ``rooms`` == 1 means the whole flat is let as one unit; > 1 means it is a
# shared flat whose rooms are let separately (each with its own tenancy).
_FLATS = [
    ("Warszawa", "Marszałkowska", "12", "5", 58, "WAW1", 1),
    ("Kraków", "Długa", "44", "3", 42, "KRK1", 1),
    ("Wrocław", "Świdnicka", "7", "10", 71, "WRO1", 4),
    ("Gdańsk", "Długi Targ", "21", "2", 36, "GDA1", 1),
]


def _aware(d: date) -> datetime:
    """Midnight of ``d`` in the current timezone, as an aware datetime."""
    return timezone.make_aware(datetime(d.year, d.month, d.day))


def _add_months(d: date, months: int) -> date:
    """Return ``d`` shifted by ``months`` (day clamped to 1st for simplicity)."""
    total = (d.year * 12 + (d.month - 1)) + months
    return date(total // 12, total % 12 + 1, 1)


def _month_iter(start: date, end: date):
    """Yield the first-of-month date for every month in [start, end]."""
    cur = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cur <= last:
        yield cur
        cur = _add_months(cur, 1)


class Command(BaseCommand):
    help = "Seed a fictional landlord + tenant with 4 years of rental history (dev only)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow running even when DEBUG is off.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="RNG seed for reproducible data (default 42).",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to seed with DEBUG off. Re-run with --force if intended."
            )

        rng = random.Random(options["seed"])
        today = timezone.localdate()
        # Four full years back, anchored to the first of the month.
        start_of_history = _add_months(date(today.year, today.month, 1), -48)

        # --- Idempotency: wipe the two demo accounts and all they own ----------
        # Contracts PROTECT their Flat/Room, so delete contracts first; then the
        # User delete cascades the rest (flats, rooms, ledger, meters, tax...).
        demo_users = User.objects.filter(email__in=[LANDLORD_EMAIL, TENANT_EMAIL])
        Contract.all_objects.filter(owner__in=demo_users).delete()
        deleted = demo_users.delete()
        if deleted[0]:
            self.stdout.write(f"Removed existing demo accounts ({deleted[0]} rows).")

        # --- Landlord -----------------------------------------------------------
        landlord = User.objects.create_user(
            username=LANDLORD_EMAIL,
            email=LANDLORD_EMAIL,
            password=DEMO_PASSWORD,
            first_name="Jan",
            last_name="Przykładowy",
            role=User.Role.LANDLORD,
            email_verified=True,
            is_active=True,
        )

        flats: list[Flat] = []
        all_contracts: list[Contract] = []

        for city, street, bno, fno, size, code, n_rooms in _FLATS:
            rental_start = _aware(start_of_history - timedelta(days=rng.randint(0, 200)))
            flat = Flat.objects.create(
                owner=landlord,
                city=city,
                street=street,
                building_no=bno,
                flat_no=fno,
                flat_size=size,
                code=code,
                room_count=n_rooms,
                created=rental_start,
                rental_start=rental_start,
            )
            flats.append(flat)

            # --- Meters (electricity + cold water) with a price and readings ---
            self._seed_meters(landlord, flat, rng, start_of_history, today)
            # --- Fixed monthly admin fees (internet, heating, funds) ------------
            self._seed_admin_fees(landlord, flat, start_of_history)
            # --- Contribution fund (składka na sprzątanie, 25 zł/mc) -----------
            Fund.objects.create(
                owner=landlord,
                flat=flat,
                name="Sprzątanie",
                monthly_amount=Decimal("25.00"),
                start_date=start_of_history,
            )

            # --- Rooms: whole flat as one unit, or several rooms let separately -
            if n_rooms == 1:
                rooms = [
                    Room.objects.create(
                        owner=landlord,
                        flat=flat,
                        unit_type=Room.UnitType.WHOLE,
                        room_no=1,
                        name="Cały lokal",
                        size=size,
                        beds=rng.randint(1, 3),
                        fee=Decimal(rng.choice([1800, 2200, 2600, 3000, 3400])),
                        deposit=Decimal(rng.choice([1800, 2200, 2600, 3000])),
                    )
                ]
            else:
                per_room = max(1, size // n_rooms)
                rooms = [
                    Room.objects.create(
                        owner=landlord,
                        flat=flat,
                        unit_type=Room.UnitType.ROOM,
                        room_no=k,
                        name=f"Pokój {k}",
                        size=per_room,
                        beds=1,
                        fee=Decimal(rng.choice([900, 1050, 1200, 1350])),
                        deposit=Decimal(rng.choice([900, 1050, 1200])),
                    )
                    for k in range(1, n_rooms + 1)
                ]

            # --- Tenancy timeline over the last 4 years, per room ---------------
            for room in rooms:
                all_contracts += self._seed_contracts(
                    landlord, flat, room, rng, start_of_history, today
                )

        # --- Rent payments: create every contract-month, drop a few (arrears) --
        slots: list[tuple[Contract, date]] = []
        for contract in all_contracts:
            assert contract.contract_start is not None
            end = min(contract.contract_end or today, today)
            for month in _month_iter(contract.contract_start, end):
                slots.append((contract, month))

        missing = set(
            rng.sample(range(len(slots)), min(N_MISSING_PAYMENTS, len(slots)))
        )
        payments = 0
        for i, (contract, month) in enumerate(slots):
            if i in missing:
                continue
            pay_day = contract.payment_day or 10
            day = max(1, min(pay_day + rng.choice([-2, 0, 0, 0, 3]), 28))
            record_date = _aware(date(month.year, month.month, day))
            LedgerEntry.objects.create(
                owner=landlord,
                flat=contract.flat,
                room=contract.room,
                contract=contract,
                short_desc="Czynsz Najmu",
                record_date=record_date,
                billing_period=month,
                created=record_date,
                modified=record_date,
                amount_in_taxable=contract.price,
                is_mortgage=False,
                kind=LedgerEntry.Kind.RENT,
            )
            payments += 1

        # --- Tenant account linked to a currently-active lease ------------------
        active = [
            c for c in all_contracts
            if c.contract_end is None or c.contract_end >= today
        ]
        tenant_contract = active[0] if active else all_contracts[-1]
        tenant = User.objects.create_user(
            username=TENANT_EMAIL,
            email=TENANT_EMAIL,
            password=DEMO_PASSWORD,
            first_name="Anna",
            last_name="Kowalska",
            role=User.Role.TENANT,
            email_verified=True,
            is_active=True,
        )
        tenant_contract.tenant_name = "Anna Kowalska"
        tenant_contract.email = TENANT_EMAIL
        tenant_contract.tenant_user = tenant
        tenant_contract.save(
            update_fields=["tenant_name", "email", "tenant_user"]
        )

        # --- Make one lease expire within a month (for the renewal reminder) ----
        # Prefer a whole-flat lease other than the tenant's, so the split-flat
        # rooms stay open-ended. Give it a tenant e-mail so the reminder can send.
        expiring = next(
            (
                c
                for c in sorted(
                    active, key=lambda c: 0 if c.flat.room_count == 1 else 1
                )
                if c is not tenant_contract
            ),
            None,
        )
        if expiring is not None:
            expiring.contract_end = today + timedelta(days=18)
            if not expiring.email:
                expiring.email = "najemca.wygasa@example.com"
            expiring.save(update_fields=["contract_end", "email"])

        # --- Tax: settle all completed months except the most recent few --------
        table = tax_service.tax_table(landlord)
        monthly = sorted(
            (m for months in table.values() for m in months if m.tax > 0),
            key=lambda m: (m.year, m.month),
        )
        cutoff = len(monthly) - N_UNPAID_TAX
        taxes_paid = 0
        for i, m in enumerate(monthly):
            if i >= cutoff:
                continue  # leave the most recent months unpaid
            paid_on = m.deadline - timedelta(days=rng.randint(0, 6))
            TaxDue.objects.create(
                owner=landlord,
                period=f"{m.month:02d}/{m.year}",
                tax_amount=m.tax,
                tax_date=_aware(paid_on),
            )
            taxes_paid += 1
        taxes_unpaid = len(monthly) - taxes_paid

        # --- Settlements: compute a few recent months per flat ------------------
        from apps.core.services.fees import save_settlement

        settle_months = 3
        settlements = 0
        for flat in flats:
            for offset in range(1, settle_months + 1):
                ps = _add_months(date(today.year, today.month, 1), -offset)
                pe = _add_months(ps, 1) - timedelta(days=1)
                calc = save_settlement(flat, ps, pe)
                if calc.tenants.exists():
                    settlements += 1
                else:
                    calc.delete()  # no active tenants that month -> drop the empty calc

        self.stdout.write(self.style.SUCCESS("Demo data seeded:"))
        self.stdout.write(f"  Landlord : {LANDLORD_EMAIL} / {DEMO_PASSWORD}")
        self.stdout.write(f"  Tenant   : {TENANT_EMAIL} / {DEMO_PASSWORD}")
        self.stdout.write(
            f"  Tenant lease: {tenant_contract.contract_number} @ {tenant_contract.flat}"
        )
        self.stdout.write(
            f"  {len(flats)} flats, {len(all_contracts)} contracts, "
            f"{payments} payments ({len(missing)} missing)."
        )
        self.stdout.write(
            f"  Tax: {taxes_paid} months paid, {taxes_unpaid} unpaid."
        )
        self.stdout.write(
            f"  Settlements: {settlements} (up to {settle_months} months per flat)."
        )

    # -- helpers ----------------------------------------------------------------
    def _seed_meters(
        self,
        landlord: User,
        flat: Flat,
        rng: random.Random,
        start: date,
        today: date,
    ) -> None:
        specs = [
            ("Prąd", MeterDefinition.Unit.KWH, Decimal("0.9200"), 30, 90),
            ("Gaz", MeterDefinition.Unit.M3, Decimal("2.8000"), 5, 20),
            ("Zimna woda", MeterDefinition.Unit.M3, Decimal("12.5000"), 2, 6),
            ("Ciepła woda", MeterDefinition.Unit.M3, Decimal("35.0000"), 1, 4),
        ]
        for name, unit, price, lo, hi in specs:
            meter = MeterDefinition.objects.create(
                owner=landlord, flat=flat, name=name, unit=unit
            )
            MeterPrice.objects.create(
                owner=landlord,
                flat=flat,
                meter=meter,
                price_date=start,
                price=price,
            )
            # A reading every ~6 months, monotonically increasing.
            value = Decimal(rng.randint(1000, 5000))
            read_day = start
            while read_day <= today:
                MeterReading.objects.create(
                    owner=landlord,
                    flat=flat,
                    meter=meter,
                    read_date=read_day,
                    value=value,
                )
                value += Decimal(rng.randint(lo, hi) * 6)
                read_day = _add_months(read_day, 6)

    def _seed_admin_fees(self, landlord: User, flat: Flat, start: date) -> None:
        """Fixed monthly admin fees (internet, heating, funds) with a price."""
        fees = [
            ("Internet", Decimal("60.00")),
            ("Ogrzewanie (ryczałt)", Decimal("200.00")),
            ("Fundusz remontowy", Decimal("80.00")),
            ("Eksploatacja", Decimal("150.00")),
        ]
        for title, price in fees:
            fee = AdminFee.objects.create(
                owner=landlord, flat=flat, title=title, is_individual=False
            )
            AdminFeePrice.objects.create(
                owner=landlord,
                flat=flat,
                admin_fee=fee,
                price_date=_aware(start),
                price=price,
            )

    def _seed_contracts(
        self,
        landlord: User,
        flat: Flat,
        room: Room,
        rng: random.Random,
        start: date,
        today: date,
    ) -> list[Contract]:
        contracts: list[Contract] = []
        cursor = start
        seq = 1
        base_rent = int(room.fee or Decimal(2200))
        while cursor < today:
            length = rng.randint(10, 20)  # months
            c_start = cursor
            c_end = _add_months(c_start, length) - timedelta(days=1)
            # A short vacancy gap before a hypothetical next tenant (1-2 months).
            next_cursor = _add_months(c_end, rng.choice([1, 1, 2]))
            # If the next tenancy would start on/after today, keep THIS one as the
            # current, open-ended (indefinite) lease so every room stays occupied.
            open_ended = next_cursor >= today
            if rng.random() < 0.5:
                first, last = rng.choice(_FEMALE_FIRST), rng.choice(_FEMALE_LAST)
            else:
                first, last = rng.choice(_MALE_FIRST), rng.choice(_MALE_LAST)
            email = f"{first}.{last}@example.com".lower().translate(_PL_ASCII)
            # Rent drifts up a little year over year.
            years_in = (c_start.year - start.year)
            rent = Decimal(base_rent + years_in * 100)
            contract = Contract.objects.create(
                owner=landlord,
                flat=flat,
                room=room,
                contract_number=f"{flat.code}/{c_start.year}/{seq:02d}",
                tenant_name=f"{first} {last}",
                email=email,
                phone=f"+48 {rng.randint(500, 799)} {rng.randint(100, 999)} "
                f"{rng.randint(100, 999)}",
                price=rent,
                deposit=rent,
                contract_date=c_start - timedelta(days=rng.randint(3, 14)),
                contract_start=c_start,
                contract_end=None if open_ended else c_end,
                payment_day=rng.choice([5, 10, 15]),
            )
            contracts.append(contract)
            seq += 1
            if open_ended:
                break
            cursor = next_cursor
        return contracts
