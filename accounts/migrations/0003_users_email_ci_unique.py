import accounts.models
from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0002_passwordresetchallenge'),
    ]

    operations = [
        migrations.AlterModelManagers(
            name='users',
            managers=[
                ('objects', accounts.models.UsersManager()),
            ],
        ),
        migrations.AddConstraint(
            model_name='users',
            constraint=models.UniqueConstraint(
                Lower('email'), name='accounts_users_email_ci_unique'
            ),
        ),
    ]
