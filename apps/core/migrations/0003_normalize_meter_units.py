from django.db import migrations


def normalize_units(apps, schema_editor):
    """Normalize legacy 'm3' meter units to 'm³' (superscript three)."""
    MeterDefinition = apps.get_model("core", "MeterDefinition")
    MeterDefinition.objects.filter(unit="m3").update(unit="m³")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_remove_taxmode_mode"),
    ]

    operations = [
        migrations.RunPython(normalize_units, noop),
    ]
