"""Domain models for the Rozlicz Najem rental-management app.

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

import secrets
from typing import Any

from django.conf import settings
from django.db import models


def _invite_token() -> str:
    """URL-safe random token for tenant contract invites."""
    return secrets.token_urlsafe(24)


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
    # Hidden BCC recipient for emails about this flat (the landlord's own copy).
    owner_bcc_email = models.EmailField(blank=True)
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
    # Open platform: the tenant's own account, once they register and link.
    tenant_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tenant_contracts",
    )
    price = _money(null=True, blank=True)
    deposit = _money(null=True, blank=True)
    contract_date = models.DateField(null=True, blank=True)
    contract_start = models.DateField(null=True, blank=True)
    contract_end = models.DateField(null=True, blank=True)
    # Legacy `deadline`: payment due day-of-month.
    payment_day = models.PositiveSmallIntegerField(null=True, blank=True)
    # Definitive termination: end date is final, no renewal reminders.
    hard_stop = models.BooleanField(default=False)
    # A renewal proposal was e-mailed to the tenant, pending landlord confirmation.
    renewal_proposed_until = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-contract_start"]

    def __str__(self) -> str:
        return f"{self.contract_number} — {self.tenant_name}"


class ContractInvite(models.Model):
    """One-time invite letting a tenant register and link to a contract.

    The landlord creates an invite for a contract; the resulting ``token`` is
    shared as a link (``/register/tenant/?invite=<token>``) or as a code the
    tenant types during self-registration. Consumed once, on registration.
    """

    contract = models.ForeignKey(
        Contract, on_delete=models.CASCADE, related_name="invites"
    )
    token = models.CharField(max_length=64, unique=True, default=_invite_token)
    email = models.EmailField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accepted_invites",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self) -> str:
        return f"Invite {self.token[:8]}… for {self.contract}"

    @property
    def is_used(self) -> bool:
        return self.accepted_by_id is not None


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
    # True when the value was estimated from past usage instead of read off the
    # physical meter. Existing (imported) readings are real, hence default False.
    is_estimated = models.BooleanField(default=False)

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


# --- Contribution funds --------------------------------------------------------
class Fund(models.Model):
    """A per-flat contribution pool (e.g. a cleaning fund).

    Each tenant pays a fixed contribution (``monthly_amount``) on top of their
    utility bills. The contribution is credited to the pool when the tenant's
    bills are confirmed as paid (a ``fee`` ledger entry), so the collected
    total equals ``monthly_amount`` times the number of confirmed fee payments
    for the flat within the fund's active window. The landlord also records
    ad-hoc contributions and the expenses paid out of the pool. The running
    balance is tracked independently of rental income — it is NOT taxable rent
    and does not appear in income/tax/ledger reports.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="funds"
    )
    flat = models.ForeignKey(Flat, on_delete=models.CASCADE, related_name="funds")
    name = models.CharField(max_length=32)
    # Fixed monthly contribution (składka / miesiąc).
    monthly_amount = _money()
    # First month the contribution accrues from.
    start_date = models.DateField()
    # Optional close month; after it the fund stops accruing (freezes balance).
    end_date = models.DateField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["flat", "name"]

    def __str__(self) -> str:
        return f"{self.name} @ {self.flat}"


class FundContribution(models.Model):
    """An ad-hoc contribution paid into a fund (top-up or correction)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="fund_contributions",
    )
    flat = models.ForeignKey(
        Flat, on_delete=models.CASCADE, related_name="fund_contributions"
    )
    fund = models.ForeignKey(
        Fund, on_delete=models.CASCADE, related_name="contributions"
    )
    contributed_on = models.DateField()
    amount = _money()
    note = models.CharField(max_length=64, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-contributed_on", "-id"]

    def __str__(self) -> str:
        return f"{self.fund}: +{self.amount} ({self.contributed_on})"


class FundExpense(models.Model):
    """A payout from a fund (e.g. cleaning supplies bought for the pool)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="fund_expenses",
    )
    flat = models.ForeignKey(
        Flat, on_delete=models.CASCADE, related_name="fund_expenses"
    )
    fund = models.ForeignKey(Fund, on_delete=models.CASCADE, related_name="expenses")
    spent_on = models.DateField()
    amount = _money()
    description = models.CharField(max_length=64)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-spent_on", "-id"]

    def __str__(self) -> str:
        return f"{self.fund}: -{self.amount} ({self.spent_on})"


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
        FUND = "Fund", "Fundusze"

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
    # The tenancy this snapshot settles. Match payments by this FK, not by the
    # ``contract_number`` label (which is not unique across renewals).
    contract = models.ForeignKey(
        Contract,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="settlement_tenants",
    )
    tenant_name = models.CharField(max_length=64, blank=True)
    contract_number = models.CharField(max_length=64, blank=True)
    email = models.EmailField(max_length=64, blank=True)

    def __str__(self) -> str:
        return self.tenant_name


# --- Finance -------------------------------------------------------------------
class LedgerEntry(models.Model):
    """Income/expense record (legacy `records`)."""

    class Kind(models.TextChoices):
        RENT = "rent", "Czynsz najmu"
        FEE = "fee", "Pozostałe opłaty"

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
    # When this entry confirms a saved settlement (Pozostałe opłaty), it links
    # back to the exact settlement tenant it settles. This is the reliable key
    # for "is this fee paid?" — contract numbers are not unique across renewals.
    settlement_tenant = models.ForeignKey(
        "FeeCalculationTenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    short_desc = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)
    # Data wpłaty — when the money was received (drives the ryczałt tax point).
    record_date = models.DateTimeField(null=True, blank=True)
    # Miesiąc rozliczeniowy — which month the payment settles (first of month).
    # Independent of record_date: a tenant may pay early or late for a month.
    billing_period = models.DateField(null=True, blank=True)
    created = models.DateTimeField(null=True, blank=True)
    modified = models.DateTimeField(null=True, blank=True)
    amount_in_taxable = _money(null=True, blank=True)
    is_mortgage = models.BooleanField(null=True, blank=True)
    # Distinguishes rent income (taxable) from other reimbursed fees (utilities,
    # admin) so that only rent counts towards income/ryczalt.
    kind = models.CharField(
        max_length=8, choices=Kind.choices, default=Kind.RENT, db_index=True
    )

    class Meta:
        ordering = ["-record_date"]
        verbose_name_plural = "ledger entries"

    def __str__(self) -> str:
        return f"{self.record_date:%Y-%m-%d} {self.short_desc}"


# --- Tax -----------------------------------------------------------------------
class TaxMode(models.Model):
    """Per-year tax settings (legacy `tax_mode`).

    Polish private rental is lump-sum (ryczałt) only since 2023, so no taxation
    mode is stored; only the settlement period (monthly/quarterly) is kept.
    """

    class Period(models.TextChoices):
        MONTHLY = "m", "Monthly"
        QUARTERLY = "q", "Quarterly"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tax_modes"
    )
    cal_year = models.PositiveIntegerField(null=True, blank=True)
    period = models.CharField(max_length=1, choices=Period.choices, blank=True)
    reminder = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.cal_year}: {self.period}"


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


# --- Wishlist / feedback -------------------------------------------------------
class WishlistItem(models.Model):
    """A problem report or feature wish submitted by a user from settings.

    Stored in the database and managed by staff from the admin (the eventual
    superadmin panel): status can be advanced and staff can reply, forming a
    conversation thread with the user (see ``WishlistMessage``).
    """

    class Kind(models.TextChoices):
        PROBLEM = "problem", "Problem / błąd"
        WISH = "wish", "Życzenie / pomysł"

    class Status(models.TextChoices):
        OPEN = "open", "Nowe"
        IN_PROGRESS = "in_progress", "W trakcie"
        DONE = "done", "Zrobione"
        REJECTED = "rejected", "Odrzucone"
        CLOSED = "closed", "Zamknięte"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wishlist_items",
    )
    kind = models.CharField(
        max_length=16, choices=Kind.choices, default=Kind.WISH
    )
    subject = models.CharField(max_length=200)
    body = models.TextField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self) -> str:
        return f"[{self.get_status_display()}] {self.subject}"

    @property
    def is_open(self) -> bool:
        return self.status not in (self.Status.DONE, self.Status.CLOSED)


class WishlistMessage(models.Model):
    """A single message in a wishlist item's conversation thread.

    Written either by the reporting user or by staff answering from the admin.
    ``from_staff`` snapshots who wrote it so replies keep their side even if the
    author account is later removed.
    """

    item = models.ForeignKey(
        WishlistItem, on_delete=models.CASCADE, related_name="messages"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wishlist_messages",
    )
    from_staff = models.BooleanField(default=False)
    body = models.TextField()
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created"]

    def __str__(self) -> str:
        who = "staff" if self.from_staff else "user"
        return f"{who} on {self.item_id}: {self.body[:40]}"


# --- Email communication -------------------------------------------------------
class EmailTemplate(models.Model):
    """A reusable e-mail template owned by a landlord.

    ``subject`` and ``body`` may contain ``{placeholder}`` tokens (e.g.
    ``{tenant_name}``, ``{flat}``, ``{period}``) filled in at send time. The two
    standard kinds have built-in defaults in ``services/mailer.py``; a stored
    active template of that kind overrides the default.
    """

    class Kind(models.TextChoices):
        CONTRACT_RENEWAL = "contract_renewal", "Przedłużenie umowy"
        SETTLEMENT = "settlement", "Rozliczenie / rachunek"
        CUSTOM = "custom", "Własny"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_templates",
    )
    kind = models.CharField(
        max_length=32, choices=Kind.choices, default=Kind.CUSTOM
    )
    name = models.CharField(max_length=120)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    is_active = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.name}"


class EmailLog(models.Model):
    """A record of one e-mail the app sent on a landlord's behalf (audit trail)."""

    class Status(models.TextChoices):
        SENT = "sent", "Wysłano"
        FAILED = "failed", "Błąd"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_logs",
    )
    flat = models.ForeignKey(
        Flat, on_delete=models.SET_NULL, null=True, blank=True, related_name="email_logs"
    )
    template = models.ForeignKey(
        "EmailTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logs",
    )
    # When this log is a settlement email, the tenant it was sent for.
    settlement_tenant = models.ForeignKey(
        "FeeCalculationTenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_logs",
    )
    subject = models.CharField(max_length=200)
    body = models.TextField()
    to = models.JSONField(default=list)
    cc = models.JSONField(default=list)
    bcc = models.JSONField(default=list)
    status = models.CharField(
        max_length=8, choices=Status.choices, default=Status.SENT
    )
    error = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self) -> str:
        return f"[{self.status}] {self.subject} → {len(self.to) + len(self.bcc)}"

    @property
    def recipient_count(self) -> int:
        return len(self.to) + len(self.cc) + len(self.bcc)
