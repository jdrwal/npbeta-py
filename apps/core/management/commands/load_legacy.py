"""Migrate data from the legacy MariaDB database into the new Django models.

Usage (inside the container, with the legacy MariaDB loaded — see README):

    python manage.py load_legacy --flush

The command connects to the legacy database via PyMySQL (env: LEGACY_DB_*),
reads each table and recreates the rows in the Django/PostgreSQL models. It:

- preserves primary keys so legacy foreign-key columns map 1:1;
- converts legacy ``float`` values to ``Decimal`` (rounded to the target field's
  precision), fixing binary-float rounding artefacts;
- maps ``deleted`` tinyint to the soft-delete flag and ``uid``/``fid``/``cid``/
  ``tid`` columns to real foreign keys;
- stores legacy MD5 passwords as ``legacy_md5$<hash>`` (upgraded to Argon2 on
  first login);
- skips orphaned child rows whose parent is missing (legacy had no FKs).
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import (
    AdminFee,
    AdminFeePrice,
    Contract,
    FeeCalculation,
    FeeCalculationItem,
    FeeCalculationTenant,
    Flat,
    LedgerEntry,
    MeterDefinition,
    MeterPrice,
    MeterReading,
    Room,
    TaxDue,
    TaxMode,
)

BATCH = 500


# --- Value converters ----------------------------------------------------------
def _dec(value: Any, places: int, default: Decimal | None = None) -> Decimal | None:
    if value is None:
        return default
    quant = Decimal(1).scaleb(-places)
    return Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)


def _dt(value: Any) -> datetime | None:
    """Coerce a legacy datetime/date to an aware UTC datetime (or None)."""
    if not value:
        return None
    if isinstance(value, str):
        if value.startswith("0000-00-00"):
            return None
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, UTC)
        return value
    # plain date -> midnight UTC
    return timezone.make_aware(datetime(value.year, value.month, value.day), UTC)


def _date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, str):
        if value.startswith("0000-00-00"):
            return None
        return date.fromisoformat(value[:10])
    if isinstance(value, datetime):
        return value.date()
    return value


def _dt_req(value: Any) -> datetime:
    """Like _dt but for NOT NULL columns (asserts a value is present)."""
    result = _dt(value)
    assert result is not None  # noqa: S101
    return result


def _date_req(value: Any) -> date:
    """Like _date but for NOT NULL columns (asserts a value is present)."""
    result = _date(value)
    assert result is not None  # noqa: S101
    return result


def _unit(value: Any) -> str:
    if not value:
        return ""
    return value.replace("&sup3;", "³")


def _fk(value: Any) -> Any:
    """Legacy 0/NULL means 'no relation'."""
    return value or None


class Command(BaseCommand):
    help = "Import data from the legacy MariaDB database into the Django models."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing rows (and non-superuser users) before importing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as exc:  # pragma: no cover
            raise CommandError("PyMySQL is required: pip install PyMySQL") from exc

        conn = pymysql.connect(
            host=os.environ.get("LEGACY_DB_HOST", "legacydb"),
            port=int(os.environ.get("LEGACY_DB_PORT", "3306")),
            user=os.environ.get("LEGACY_DB_USER", "root"),
            password=os.environ.get("LEGACY_DB_PASSWORD", "legacy"),
            database=os.environ.get("LEGACY_DB_NAME", "np"),
            charset="utf8mb4",
            cursorclass=DictCursor,
        )
        self.stdout.write("Connected to legacy database.")

        try:
            with conn.cursor() as cur, transaction.atomic():
                if options["flush"]:
                    self._flush()
                self._load(cur)
                self._reset_sequences()
        finally:
            conn.close()

        self.stdout.write(self.style.SUCCESS("Legacy import complete."))

    # -- helpers ----------------------------------------------------------------
    def _rows(self, cur: Any, table: str) -> list[dict[str, Any]]:
        cur.execute(f"SELECT * FROM `{table}`")
        return list(cur.fetchall())

    def _report(self, name: str, created: int, skipped: int = 0) -> None:
        msg = f"  {name:<22} {created:>6} imported"
        if skipped:
            msg += f", {skipped} skipped"
        self.stdout.write(msg)

    def _flush(self) -> None:
        self.stdout.write("Flushing existing data...")
        for model in (
            TaxDue,
            TaxMode,
            LedgerEntry,
            FeeCalculationTenant,
            FeeCalculationItem,
            FeeCalculation,
            AdminFeePrice,
            AdminFee,
            MeterPrice,
            MeterReading,
            MeterDefinition,
        ):
            model.objects.all().delete()
        for sd_model in (Contract, Room, Flat):
            sd_model.all_objects.all().delete()
        User.objects.filter(is_superuser=False).delete()

    def _reset_sequences(self) -> None:
        models = [
            User, Flat, Room, Contract, MeterDefinition, MeterReading, MeterPrice,
            AdminFee, AdminFeePrice, FeeCalculation, FeeCalculationItem,
            FeeCalculationTenant, LedgerEntry, TaxMode, TaxDue,
        ]
        statements = connection.ops.sequence_reset_sql(no_style(), models)
        if statements:
            with connection.cursor() as cur:
                for sql in statements:
                    cur.execute(sql)

    # -- the actual load --------------------------------------------------------
    def _load(self, cur: Any) -> None:  # noqa: PLR0915 (linear ETL is clearer inline)
        # 1. Users
        users = []
        for r in self._rows(cur, "users"):
            email = r["email"] or ""
            pw = r["password"]
            users.append(
                User(
                    id=r["id"],
                    username=email or f"user{r['id']}",
                    email=email,
                    first_name=r["name"] or "",
                    last_name=r["sname"] or "",
                    password=f"legacy_md5${pw}" if pw else "!unusable",
                    is_active=True,
                    date_joined=_dt(r["created"]) or timezone.now(),
                    forecast_start_day=r["fore_start"] or 20,
                )
            )
        User.objects.bulk_create(users, batch_size=BATCH)
        user_ids = {u.id for u in users}
        default_owner = min(user_ids) if user_ids else None
        self._report("users", len(users))

        def owner(uid: Any) -> Any:
            return uid if uid in user_ids else default_owner

        # 2. Flats
        flats = [
            Flat(
                id=r["id"],
                owner_id=owner(r["user_id"]),
                is_deleted=bool(r["deleted"]),
                city=r["city"] or "",
                street=r["street"] or "",
                building_no=r["bldg_no"] or "",
                flat_no=r["flat_no"] or "",
                flat_size=r["flat_size"],
                code=r["code"] or "",
                room_count=r["rooms"],
                created=_dt(r["created"]),
                rental_start=_dt(r["rental_start"]),
                color=r["color"] or "",
                mortgage_start=_dt(r["mort_start"]),
                mortgage_amount=_dec(r["mort_am"], 2),
                mortgage_capital=_dec(r["mort_cap"], 2),
                mortgage_interest=_dec(r["mort_int"], 2),
            )
            for r in self._rows(cur, "flats")
        ]
        Flat.all_objects.bulk_create(flats, batch_size=BATCH)
        flat_ids = {f.id for f in flats}
        self._report("flats", len(flats))

        # 3. Rooms
        rooms = [
            Room(
                id=r["id"],
                owner_id=owner(r["uid"]),
                is_deleted=bool(r["deleted"]),
                flat_id=r["flat_id"],
                room_no=r["room_no"],
                name=r["rname"] or "",
                size=r["size"],
                beds=r["beds"],
                fee=_dec(r["fee"], 2),
                deposit=_dec(r["deposit"], 2),
            )
            for r in self._rows(cur, "rooms")
            if r["flat_id"] in flat_ids
        ]
        Room.all_objects.bulk_create(rooms, batch_size=BATCH)
        room_ids = {r.id for r in rooms}
        self._report("rooms", len(rooms))

        # 4. Contracts
        src = self._rows(cur, "contracts")
        contracts = [
            Contract(
                id=r["id"],
                owner_id=owner(r["uid"]),
                is_deleted=bool(r["deleted"]),
                flat_id=r["flat_id"],
                room_id=r["room_id"],
                contract_number=r["contr_number"] or "",
                tenant_name=r["tname"] or "",
                email=r["email"] or "",
                phone=r["phone"] or "",
                price=_dec(r["price"], 2),
                deposit=_dec(r["deposit"], 2),
                contract_date=_date(r["contract_date"]),
                contract_start=_date(r["contract_start"]),
                contract_end=_date(r["contract_end"]),
                payment_day=r["deadline"],
            )
            for r in src
            if r["flat_id"] in flat_ids and r["room_id"] in room_ids
        ]
        Contract.all_objects.bulk_create(contracts, batch_size=BATCH)
        contract_ids = {c.id for c in contracts}
        self._report("contracts", len(contracts), len(src) - len(contracts))

        # 5. Meter definitions
        meters = [
            MeterDefinition(
                id=r["id"],
                owner_id=owner(r["uid"]),
                flat_id=r["fid"],
                name=r["cname"] or "",
                unit=_unit(r["unit"]),
            )
            for r in self._rows(cur, "counterdef")
            if r["fid"] in flat_ids
        ]
        MeterDefinition.objects.bulk_create(meters, batch_size=BATCH)
        meter_ids = {m.id for m in meters}
        self._report("meters", len(meters))

        # 6. Meter readings
        src = self._rows(cur, "counterstate")
        readings = [
            MeterReading(
                id=r["id"],
                owner_id=owner(r["uid"]),
                flat_id=r["fid"],
                meter_id=r["cid"],
                read_date=_date_req(r["read_date"]),
                value=_dec(r["read_value"], 3, Decimal("0")),
            )
            for r in src
            if r["cid"] in meter_ids and r["fid"] in flat_ids and _date(r["read_date"])
        ]
        MeterReading.objects.bulk_create(readings, batch_size=BATCH)
        self._report("meter readings", len(readings), len(src) - len(readings))

        # 7. Meter prices
        src = self._rows(cur, "counterprice")
        prices = [
            MeterPrice(
                id=r["id"],
                owner_id=owner(r["uid"]),
                flat_id=r["fid"],
                meter_id=r["cid"],
                price_date=_date(r["price_date"]),
                price=_dec(r["price_value"], 4, Decimal("0")),
            )
            for r in src
            if r["cid"] in meter_ids and r["fid"] in flat_ids
        ]
        MeterPrice.objects.bulk_create(prices, batch_size=BATCH)
        self._report("meter prices", len(prices), len(src) - len(prices))

        # 8. Admin fees
        adminfees = [
            AdminFee(
                id=r["id"],
                owner_id=owner(r["uid"]),
                flat_id=r["fid"],
                title=r["title"] or "",
                is_individual=bool(r["is_individual"]),
            )
            for r in self._rows(cur, "adminfee")
            if r["fid"] in flat_ids
        ]
        AdminFee.objects.bulk_create(adminfees, batch_size=BATCH)
        adminfee_ids = {a.id for a in adminfees}
        self._report("admin fees", len(adminfees))

        # 9. Admin fee prices
        src = self._rows(cur, "adminprice")
        adminprices = [
            AdminFeePrice(
                id=r["id"],
                owner_id=owner(r["uid"]),
                flat_id=r["fid"],
                admin_fee_id=r["tid"],
                price_date=_dt(r["price_date"]),
                price=_dec(r["price"], 2),
            )
            for r in src
            if r["tid"] in adminfee_ids and r["fid"] in flat_ids
        ]
        AdminFeePrice.objects.bulk_create(adminprices, batch_size=BATCH)
        self._report("admin fee prices", len(adminprices), len(src) - len(adminprices))

        # 10. Fee calculations
        calcs = [
            FeeCalculation(
                id=r["id"],
                owner_id=owner(r["uid"]),
                flat_id=r["fid"],
                stamp=_dt_req(r["stamp"]),
                period_start=_dt_req(r["period_start"]),
                period_end=_dt_req(r["period_end"]),
            )
            for r in self._rows(cur, "duecalc")
            if r["fid"] in flat_ids
        ]
        FeeCalculation.objects.bulk_create(calcs, batch_size=BATCH)
        calc_ids = {c.id for c in calcs}
        self._report("fee calculations", len(calcs))

        # 11. Fee calculation tenants (must precede items: duefees.dtid -> duetens.id)
        src = self._rows(cur, "duetens")
        tenants = [
            FeeCalculationTenant(
                id=r["id"],
                owner_id=owner(r["uid"]),
                flat_id=r["fid"],
                calculation_id=r["did"],
                tenant_name=r["tname"] or "",
                contract_number=r["contractno"] or "",
                email=r["email"] or "",
            )
            for r in src
            if r["did"] in calc_ids
        ]
        FeeCalculationTenant.objects.bulk_create(tenants, batch_size=BATCH)
        tenant_ids = {t.id for t in tenants}
        self._report("calc tenants", len(tenants), len(src) - len(tenants))

        # 12. Fee calculation items (legacy dtid references duetens.id)
        src = self._rows(cur, "duefees")
        items = [
            FeeCalculationItem(
                id=r["id"],
                owner_id=owner(r["uid"]),
                flat_id=r["fid"],
                tenant_id=r["dtid"],
                fee_type=r["dtype"],
                name=r["dname"] or "",
                usage=_dec(r["dusage"], 4),
                value=_dec(r["dvalue"], 2, Decimal("0")),
            )
            for r in src
            if r["dtid"] in tenant_ids
        ]
        FeeCalculationItem.objects.bulk_create(items, batch_size=BATCH)
        self._report("calc items", len(items), len(src) - len(items))

        # 13. Ledger entries
        ledger = [
            LedgerEntry(
                id=r["id"],
                owner_id=owner(r["uid"]),
                flat_id=r["flat_id"],
                room_id=_fk(r["room_id"]) if _fk(r["room_id"]) in room_ids else None,
                contract_id=(
                    _fk(r["cont_id"]) if _fk(r["cont_id"]) in contract_ids else None
                ),
                short_desc=r["short_desc"] or "",
                notes=r["notes"] or "",
                record_date=_dt(r["record_date"]),
                created=_dt(r["created"]),
                modified=_dt(r["modified"]),
                amount_in_taxable=_dec(r["amount_in_taxable"], 2),
                is_mortgage=None if r["mort"] is None else bool(r["mort"]),
            )
            for r in self._rows(cur, "records")
            if r["flat_id"] in flat_ids
        ]
        LedgerEntry.objects.bulk_create(ledger, batch_size=BATCH)
        self._report("ledger entries", len(ledger))

        # 14. Tax mode
        taxmodes = [
            TaxMode(
                id=r["id"],
                owner_id=owner(r["uid"]),
                cal_year=r["cal_year"],
                period=r["tax_period"] or "",
                reminder=r["tax_reminder"],
            )
            for r in self._rows(cur, "tax_mode")
        ]
        TaxMode.objects.bulk_create(taxmodes, batch_size=BATCH)
        self._report("tax modes", len(taxmodes))

        # 15. Tax due
        taxdue = [
            TaxDue(
                id=r["id"],
                owner_id=owner(r["uid"]),
                period=r["period"] or "",
                tax_date=_dt(r["tax_date"]),
                tax_amount=r["tax_amount"],
            )
            for r in self._rows(cur, "taxdue")
        ]
        TaxDue.objects.bulk_create(taxdue, batch_size=BATCH)
        self._report("tax due", len(taxdue))
