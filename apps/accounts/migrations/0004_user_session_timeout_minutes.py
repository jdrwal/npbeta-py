from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_mailsettings_use_default'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='session_timeout_minutes',
            field=models.PositiveSmallIntegerField(default=30),
        ),
    ]
