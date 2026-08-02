from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_contract_tenant_user_contractinvite'),
    ]

    operations = [
        # New flag defaults to False, so every existing (imported) reading is
        # recorded as a real meter reading, not an estimate.
        migrations.AddField(
            model_name='meterreading',
            name='is_estimated',
            field=models.BooleanField(default=False),
        ),
    ]
