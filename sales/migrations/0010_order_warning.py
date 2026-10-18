from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('sales', '0009_order_sales_order_history_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='warning',
            field=models.TextField(blank=True, default=''),
        ),
    ]
