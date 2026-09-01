import django.db.models.deletion
from django.db import migrations, models


def backfill_order_transaction_customers(apps, schema_editor):
    Order = apps.get_model('sales', 'Order')
    Transaction = apps.get_model('sales', 'Transaction')
    for order in Order.objects.exclude(transaction_id=None).iterator():
        Transaction.objects.filter(
            pk=order.transaction_id, customer_id=None
        ).update(customer_id=order.customer_id)


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0002_alter_order_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaction',
            name='customer',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='transactions',
                to='sales.customer',
            ),
        ),
        migrations.RunPython(
            backfill_order_transaction_customers,
            migrations.RunPython.noop,
        ),
    ]
