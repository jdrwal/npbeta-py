from django.db import migrations, models


def backfill_unit_type(apps, schema_editor):
    """Classify existing units: a premise with a single unit is a whole-premise
    let (``whole``); premises split into several units keep them as rooms."""
    Room = apps.get_model("core", "Room")
    Flat = apps.get_model("core", "Flat")
    for flat in Flat.objects.iterator():
        rooms = list(Room.objects.filter(flat=flat))
        unit_type = "whole" if len(rooms) <= 1 else "room"
        for room in rooms:
            if room.unit_type != unit_type:
                room.unit_type = unit_type
                room.save(update_fields=["unit_type"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_feeitempayment"),
    ]

    operations = [
        migrations.AddField(
            model_name="room",
            name="unit_type",
            field=models.CharField(
                choices=[("whole", "Cały lokal"), ("room", "Pokój w lokalu")],
                default="whole",
                max_length=8,
            ),
        ),
        migrations.RunPython(backfill_unit_type, noop),
    ]
