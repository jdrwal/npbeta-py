"""Domain models for the npbeta rental-management app.

Modelled from the live database schema (data-migration/20260730_np_dump.sql),
which is the source of truth. Notable choices vs. the legacy MariaDB schema:

- All monetary/usage/meter values use ``DecimalField`` (legacy used ``float``).
- Real ``ForeignKey`` relations replace the loose ``uid``/``fid``/``cid``/``tid``
  integer columns.
- ``deleted`` tinyint becomes a soft-delete (``is_deleted``) with a default
  manager that hides deleted rows.
- Legacy ``vrates`` and ``fares`` tables are intentionally omitted: they are
  superseded by the normalized meter model (MeterDefinition/Reading/Price) and
  admin-fee model, and are dead code in the deployed app.
"""

from typing import Any

from django.conf import settings
from django.db import models


# --- Decimal field factories (fixed precision + passthrough kwargs) -------------
def _money(**kwargs: Any) -> models.DecimalField:
    """PLN amount."""
    return models.DecimalField(max_digits=10, decimal_places=2, **kwargs)


def _unit_price(**kwargs: Any) -> models.DecimalField:
    """Price per kWh / m³ / unit."""
    return models.DecimalField(max_digits=10, decimal_places=4, **kwargs)


def _usage(**kwargs: Any) -> models.DecimalField:
    """Apportioned usage share."""
    return models.DecimalField(max_digits=12, decimal_places=4, **kwargs)


def _reading(**kwargs: Any) -> models.DecimalField:
    """Raw meter reading."""
    return models.DecimalField(max_digits=12, decimal_places=3, **kwargs)


# --- Soft delete ---------------------------------------------------------------
class SoftDeleteManager(models.Manager):
    """Default manager that hides soft-deleted rows."""

    def get_queryset(self) -> models.QuerySet:
        return super().get_queryset().filter(is_deleted=False)


class SoftDeleteModel(models.Model):
    is_deleted = models.BooleanField(default=False)

    objects = SoftDeleteManager()
    all_objects = models.Manager()  # includes soft-deleted rows  # noqa: DJ012

    class Meta:
        abstract = True

    def soft_delete(self) -> None:
        self.is_deleted = True
        self.save(update_fields=["is_deleted"])


# --- Properties ----------------------------------------------------------------
class Flat(SoftDeleteModel):
    """A rental flat (legacy `flats`)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="flats"
    )
    city = models.CharField(max_length=32)
    street = models.CharField(max_length=64)
    building_no = models.CharField(max_length=15, blank=True)
    flat_no = models.CharField(max_length=15, blank=True)
    flat_size = models.PositiveIntegerField(null=True, blank=True, help_text="m²")
    code = models.CharField(max_length=16)
    room_count = models.PositiveIntegerField(null=True, blank=True)
    created = models.DateTimeField(null=True, blank=True)
    rental_start = models.DateTimeField(null=True, blank=True)
    color = models.CharField(max_length=15, blank=True)
    # Mortgage
    mortgage_start = models.DateTimeField(null=True, blank=True)
    mortgage_amount = _money(null=True, blank=True)
    mortgage_capital = _money(null=True, blank=True)
    mortgage_interest = _money(null=True, blank=True)

    class Meta:
        ordering = ["city", "street"]

    def __str__(self) -> str:
        return f"{self.city}, {self.street} {self.building_no}/{self.flat_no}".strip()


class Room(SoftDeleteModel):
    """A rentable room inside a flat (legacy `rooms`)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rooms"
    )
    flat = models.ForeignKey(Flat, on_delete=models.CASCADE, related_name="rooms")
    room_no = models.PositiveIntegerField(null=True, blank=True)
    name = models.CharField(max_length=32, blank=True)
    size = models.PositiveIntegerField(null=True, blank=True, help_text="m²")
    beds = models.PositiveIntegerField(null=True, blank=True)
    fee = _money(null=True, blank=True)
    deposit = _money(null=True, blank=True)

    class Meta:
        ordering = ["flat", "room_no"]

    def __str__(self) -> str:
        return f"{self.name or self.room_no} @ {self.flat}"


class Contract(SoftDeleteModel):
    """A tenancy contract (legacy `contracts`)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="contracts"
    )
    flat = models.ForeignKey(Flat, on_delete=models.PROTECT, related_name="contracts")
    room = models.ForeignKey(Room, on_delete=models.PROTECT, related_name="contracts")
    contract_number = models.CharField(max_length=32, blank=True)
    tenant_name = models.CharField(max_length=64, blank=True)
    email = models.EmailField(max_length=64, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    price = _money(null=True, blank=True)
    deposit = _money(null=True, blank=True)
    contract_date = models.DateField(null=True, blank=True)
    contract_start = models.DateField(null=True, blank=True)
    contract_end = models.DateField(null=True, blank=True)
    # Legacy `deadline`: payment due day-of-month.
    payment_day = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-contract_start"]

    def __str__(self) -> str:
        return f"{self.contract_number} — {self.tenant_name}"


# --- Meters --------------------------------------------------------------------
class MeterDefinition(models.Model):
    """A utility meter defined for a flat (legacy `counterdef`)."""

    class Unit(models.TextChoices):
        KWH = "kWh", "kWh"
        M3 = "m³", "m³"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="meters"
    )
    flat = models.ForeignKey(Flat, on_delete=models.CASCADE, related_name="meters")
    name = models.CharField(max_length=32)
    unit = models.CharField(max_length=8, choices=Unit.choices, blank=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.unit})"


class MeterReading(models.Model):
    """A dated meter reading (legacy `counterstate`)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="readings"
    )
    flat = models.ForeignKey(Flat, on_delete=models.CASCADE, related_name="readings")
    meter = models.ForeignKey(
        MeterDefinition, on_delete=models.CASCADE, related_name="readings"
    )
    read_date = models.DateField()
    value = _reading()

    class Meta:
        ordering = ["meter", "read_date"]

    def __str__(self) -> str:
        return f"{self.meter} @ {self.read_date}: {self.value}"


class MeterPrice(models.Model):
    """Unit price of a meter, effective from a date (legacy `counterprice`)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="meter_prices"
    )
    flat = models.ForeignKey(Flat, on_delete=models.CASCADE, related_name="meter_prices")
    meter = models.ForeignKey(
        MeterDefinition, on_delete=models.CASCADE, related_name="prices"
    )
    price_date = models.DateField(null=True, blank=True)
    price = _unit_price()

    class Meta:
        ordering = ["meter", "price_date"]

    def __str__(self) -> str:
        return f"{self.meter} from {self.price_date}: {self.price}"


# --- Administrative fees -------------------------------------------------------
class AdminFee(models.Model):
    """A fixed administrative fee type for a flat (legacy `adminfee`)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="admin_fees"
    )
    flat = models.ForeignKey(Flat, on_delete=models.CASCADE, related_name="admin_fees")
    title = models.CharField(max_length=32, blank=True)
    is_individual = models.BooleanField(default=False)

    def __str__(self) -> str:
        return self.title


class AdminFeePrice(models.Model):
    """Price of an admin fee, effective from a date (legacy `adminprice`)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="admin_fee_prices"
    )
    flat = models.ForeignKey(
        Flat, on_delete=models.CASCADE, related_name="admin_fee_prices"
    )
    admin_fee = models.ForeignKey(
        AdminFee, on_delete=models.CASCADE, related_name="prices"
    )
    price_date = models.DateTimeField(null=True, blank=True)
    price = _money(null=True, blank=True)

    class Meta:
        ordering = ["admin_fee", "price_date"]

    def __str__(self) -> str:
        return f"{self.admin_fee} from {self.price_date}: {self.price}"


# --- Saved fee calculations ----------------------------------------------------
class FeeCalculation(models.Model):
    """A saved utility settlement for a flat and period (legacy `duecalc`)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calculations"
    )
    flat = models.ForeignKey(Flat, on_delete=models.CASCADE, related_name="calculations")
    stamp = models.DateTimeField()
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()

    class Meta:
        ordering = ["-period_start"]

    def __str__(self) -> str:
        return f"{self.flat} {self.period_start:%Y-%m-%d}–{self.period_end:%Y-%m-%d}"


class FeeCalculationItem(models.Model):
    """A single fee line for one tenant within a calculation (legacy `duefees`)."""

    class FeeType(models.TextChoices):
        COUNTER = "Counter", "Counter"
        ADMIN = "Admin", "Admin"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calc_items"
    )
    flat = models.ForeignKey(Flat, on_delete=models.CASCADE, related_name="calc_items")
    # Legacy `dtid` references duetens.id: each fee line belongs to a tenant.
    tenant = models.ForeignKey(
        "FeeCalculationTenant", on_delete=models.CASCADE, related_name="items"
    )
    fee_type = models.CharField(max_length=8, choices=FeeType.choices)
    name = models.CharField(max_length=64)
    usage = _usage(null=True, blank=True)
    value = _money()

    def __str__(self) -> str:
        return f"{self.name}: {self.value}"


class FeeCalculationTenant(models.Model):
    """Tenant snapshot attached to a saved calculation (legacy `duetens`)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calc_tenants"
    )
    flat = models.ForeignKey(Flat, on_delete=models.CASCADE, related_name="calc_tenants")
    calculation = models.ForeignKey(
        FeeCalculation, on_delete=models.CASCADE, related_name="tenants"
    )
    tenant_name = models.CharField(max_length=64, blank=True)
    contract_number = models.CharField(max_length=64, blank=True)
    email = models.EmailField(max_length=64, blank=True)

    def __str__(self) -> str:
        return self.tenant_name


# --- Finance -------------------------------------------------------------------
class LedgerEntry(models.Model):
    """Income/expense record (legacy `records`)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ledger_entries"
    )
    flat = models.ForeignKey(Flat, on_delete=models.CASCADE, related_name="ledger_entries")
    room = models.ForeignKey(
        Room, on_delete=models.SET_NULL, null=True, blank=True, related_name="ledger_entries"
    )
    contract = models.ForeignKey(
        Contract,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    short_desc = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)
    record_date = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(null=True, blank=True)
    modified = models.DateTimeField(null=True, blank=True)
    amount_in_not_taxable = _money(null=True, blank=True)
    amount_in_taxable = _money(null=True, blank=True)
    amount_out = _money(null=True, blank=True)
    cost = _money(null=True, blank=True)
    is_mortgage = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ["-record_date"]
        verbose_name_plural = "ledger entries"

    def __str__(self) -> str:
        return f"{self.record_date:%Y-%m-%d} {self.short_desc}"


# --- Tax -----------------------------------------------------------------------
class TaxMode(models.Model):
    """Per-year tax settings (legacy `tax_mode`)."""

    class Mode(models.TextChoices):
        FLAT_RATE = "r", "Ryczałt"
        SCALE_18 = "L18", "Skala 18%"
        SCALE_32 = "L32", "Skala 32%"

    class Period(models.TextChoices):
        MONTHLY = "m", "Monthly"
        QUARTERLY = "q", "Quarterly"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tax_modes"
    )
    cal_year = models.PositiveIntegerField(null=True, blank=True)
    mode = models.CharField(max_length=3, choices=Mode.choices, blank=True)
    period = models.CharField(max_length=1, choices=Period.choices, blank=True)
    reminder = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.cal_year}: {self.mode}"


class TaxDue(models.Model):
    """A tax payment for a period (legacy `taxdue`). Amount is whole PLN."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tax_due"
    )
    period = models.CharField(max_length=7, blank=True, help_text="YYYY-MM")
    tax_date = models.DateTimeField(null=True, blank=True)
    tax_amount = models.IntegerField()

    class Meta:
        ordering = ["-period"]
        verbose_name_plural = "tax due"

    def __str__(self) -> str:
        return f"{self.period}: {self.tax_amount}"
