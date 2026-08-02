from typing import cast

from django.contrib import admin
from django.db.models.query import QuerySet
from django.forms.models import BaseInlineFormSet
from django.http import HttpRequest

from apps.accounts.models import User

from .models import (
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
    WishlistItem,
    WishlistMessage,
)


@admin.register(Flat)
class FlatAdmin(admin.ModelAdmin):
    list_display = ("id", "city", "street", "building_no", "flat_no", "is_deleted")
    list_filter = ("is_deleted", "city")
    search_fields = ("city", "street", "code")


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("id", "flat", "room_no", "name", "beds", "fee", "is_deleted")
    list_filter = ("is_deleted", "flat")


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "contract_number",
        "tenant_name",
        "flat",
        "room",
        "contract_start",
        "contract_end",
        "is_deleted",
    )
    list_filter = ("is_deleted", "flat")
    search_fields = ("contract_number", "tenant_name", "email")


@admin.register(MeterDefinition)
class MeterDefinitionAdmin(admin.ModelAdmin):
    list_display = ("id", "flat", "name", "unit")
    list_filter = ("flat", "unit")


@admin.register(MeterReading)
class MeterReadingAdmin(admin.ModelAdmin):
    list_display = ("id", "meter", "read_date", "value")
    list_filter = ("meter",)
    date_hierarchy = "read_date"


@admin.register(MeterPrice)
class MeterPriceAdmin(admin.ModelAdmin):
    list_display = ("id", "meter", "price_date", "price")
    list_filter = ("meter",)


@admin.register(AdminFee)
class AdminFeeAdmin(admin.ModelAdmin):
    list_display = ("id", "flat", "title", "is_individual")
    list_filter = ("flat", "is_individual")


@admin.register(AdminFeePrice)
class AdminFeePriceAdmin(admin.ModelAdmin):
    list_display = ("id", "admin_fee", "price_date", "price")
    list_filter = ("admin_fee",)


class FeeCalculationItemInline(admin.TabularInline):
    model = FeeCalculationItem
    extra = 0


class FeeCalculationTenantInline(admin.TabularInline):
    model = FeeCalculationTenant
    extra = 0


@admin.register(FeeCalculation)
class FeeCalculationAdmin(admin.ModelAdmin):
    list_display = ("id", "flat", "period_start", "period_end", "stamp")
    list_filter = ("flat",)
    date_hierarchy = "period_start"
    inlines = (FeeCalculationTenantInline,)


@admin.register(FeeCalculationTenant)
class FeeCalculationTenantAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant_name", "contract_number", "calculation")
    list_filter = ("flat",)
    search_fields = ("tenant_name", "contract_number", "email")
    inlines = (FeeCalculationItemInline,)


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "record_date",
        "billing_period",
        "flat",
        "short_desc",
        "amount_in_taxable",
        "is_mortgage",
    )
    list_filter = ("flat", "is_mortgage")
    search_fields = ("short_desc", "notes")
    date_hierarchy = "record_date"


@admin.register(TaxMode)
class TaxModeAdmin(admin.ModelAdmin):
    list_display = ("id", "cal_year", "period", "reminder")


@admin.register(TaxDue)
class TaxDueAdmin(admin.ModelAdmin):
    list_display = ("id", "period", "tax_date", "tax_amount")


class WishlistMessageInline(admin.TabularInline):
    model = WishlistMessage
    extra = 1
    fields = ("from_staff", "author", "body", "created")
    readonly_fields = ("created",)


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ("id", "subject", "kind", "status", "user", "created", "updated")
    list_filter = ("status", "kind")
    list_editable = ("status",)
    search_fields = ("subject", "body", "user__username", "user__email")
    date_hierarchy = "created"
    readonly_fields = ("user", "kind", "subject", "body", "created", "updated")
    inlines = (WishlistMessageInline,)

    def save_formset(
        self, request: HttpRequest, form: object, formset: BaseInlineFormSet, change: bool
    ) -> None:
        """Stamp new inline replies as staff messages authored by the admin."""
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, WishlistMessage) and obj.author_id is None:
                obj.author = cast(User, request.user)
                obj.from_staff = True
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()


@admin.register(WishlistMessage)
class WishlistMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "item", "from_staff", "author", "created")
    list_filter = ("from_staff",)
    search_fields = ("body",)
    date_hierarchy = "created"

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return super().get_queryset(request).select_related("item", "author")
