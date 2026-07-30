from django.contrib import admin

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
    inlines = (FeeCalculationTenantInline, FeeCalculationItemInline)


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "record_date",
        "flat",
        "short_desc",
        "amount_in_taxable",
        "amount_out",
        "is_mortgage",
    )
    list_filter = ("flat", "is_mortgage")
    search_fields = ("short_desc", "notes")
    date_hierarchy = "record_date"


@admin.register(TaxMode)
class TaxModeAdmin(admin.ModelAdmin):
    list_display = ("id", "cal_year", "mode", "period", "reminder")


@admin.register(TaxDue)
class TaxDueAdmin(admin.ModelAdmin):
    list_display = ("id", "period", "tax_date", "tax_amount")
