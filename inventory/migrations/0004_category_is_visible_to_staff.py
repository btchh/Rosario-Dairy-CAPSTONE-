from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0003_alter_ingredientbatch_batch_number_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='category',
            name='is_visible_to_staff',
            field=models.BooleanField(default=True),
        ),
    ]
