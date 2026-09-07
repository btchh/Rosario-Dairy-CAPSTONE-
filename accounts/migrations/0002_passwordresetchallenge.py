from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PasswordResetChallenge',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('otp_digest', models.CharField(max_length=64)),
                ('expires_at', models.DateTimeField()),
                ('failed_attempts', models.PositiveSmallIntegerField(default=0)),
                ('last_sent_at', models.DateTimeField()),
                (
                    'user',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='password_reset_challenge',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
