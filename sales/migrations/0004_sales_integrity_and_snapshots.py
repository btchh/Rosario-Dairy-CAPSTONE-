from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


def backfill_transaction_item_snapshots(apps, schema_editor):
    TransactionItem = apps.get_model('sales', 'TransactionItem')
    pending = []
    queryset = TransactionItem.objects.select_related(
        'product_batch__product__category'
    )
    for item in queryset.iterator(chunk_size=500):
        product = item.product_batch.product
        item.product_id_snapshot = product.pk
        item.product_name_snapshot = product.name
        item.product_variant_snapshot = product.variant
        item.category_id_snapshot = product.category_id
        item.category_name_snapshot = product.category.name
        pending.append(item)
        if len(pending) == 500:
            TransactionItem.objects.bulk_update(pending, [
                'product_id_snapshot', 'product_name_snapshot',
                'product_variant_snapshot', 'category_id_snapshot',
                'category_name_snapshot',
            ])
            pending.clear()
    if pending:
        TransactionItem.objects.bulk_update(pending, [
            'product_id_snapshot', 'product_name_snapshot',
            'product_variant_snapshot', 'category_id_snapshot',
            'category_name_snapshot',
        ])


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0003_transaction_customer'),
    ]

    operations = [
        migrations.AddField(
            model_name='transactionitem',
            name='category_id_snapshot',
            field=models.PositiveBigIntegerField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name='transactionitem',
            name='category_name_snapshot',
            field=models.CharField(editable=False, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='transactionitem',
            name='product_id_snapshot',
            field=models.PositiveBigIntegerField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name='transactionitem',
            name='product_name_snapshot',
            field=models.CharField(editable=False, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='transactionitem',
            name='product_variant_snapshot',
            field=models.CharField(blank=True, editable=False, max_length=100, null=True),
        ),
        migrations.RunPython(
            backfill_transaction_item_snapshots,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='order',
            name='discount_value',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=20),
        ),
        migrations.AlterField(
            model_name='order',
            name='transaction',
            field=models.OneToOneField(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='sales.transaction',
            ),
        ),
        migrations.AlterField(
            model_name='orderitem',
            name='subtotal',
            field=models.DecimalField(decimal_places=2, max_digits=20),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='amount_tendered',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=20, null=True),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='change_due',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=20, null=True),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='discount_amount',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=20),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='discount_value',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=20),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='subtotal',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=20),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='total_amount',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=20),
        ),
    ]
