from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('systemsetting', '0002_notificationstate'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='notificationsettings',
            name='forecast_warnings',
        ),
        migrations.RemoveField(
            model_name='notificationsettings',
            name='report_ready_notifications',
        ),
    ]
