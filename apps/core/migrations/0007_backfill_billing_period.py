from django.db import migrations


def backfill_billing_period(apps, schema_editor):
    """Set billing_period to the first day of each entry's record_date month,
    matching the previous (date-derived) behaviour."""
    LedgerEntry = apps.get_model("core", "LedgerEntry")
    for entry in LedgerEntry.objects.filter(
        billing_period__isnull=True, record_date__isnull=False
    ).iterator():
        rd = entry.record_date
        entry.billing_period = rd.date().replace(day=1)
        entry.save(update_fields=["billing_period"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_ledgerentry_billing_period"),
    ]

    operations = [
        migrations.RunPython(backfill_billing_period, noop),
    ]
