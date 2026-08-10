from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0022_room_unit_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="emailtemplate",
            name="kind",
            field=models.CharField(
                choices=[
                    ("contract_renewal", "Przedłużenie umowy"),
                    ("payment_reminder", "Ponaglenie o płatność"),
                    ("settlement", "Rozliczenie / rachunek"),
                    ("custom", "Własny"),
                ],
                default="custom",
                max_length=32,
            ),
        ),
    ]
