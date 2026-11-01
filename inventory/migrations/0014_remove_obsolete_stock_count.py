from django.db import migrations


def ensure_stock_counts_are_empty(apps, schema_editor):
    """Refuse to discard count history on an installation that used the feature."""
    stock_count = apps.get_model('inventory', 'StockCount')
    if schema_editor.connection.vendor == 'postgresql':
        table = schema_editor.quote_name(stock_count._meta.db_table)
        # Block writes until the check and table removal commit together.
        schema_editor.execute(f'LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE')
    if stock_count.objects.using(schema_editor.connection.alias).exists():
        raise RuntimeError(
            'Stock Count removal stopped: existing count records must be backed '
            'up and explicitly archived before this migration can run.'
        )


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0013_stock_adjustment_submission'),
    ]

    operations = [
        migrations.RunPython(ensure_stock_counts_are_empty, migrations.RunPython.noop),
        migrations.DeleteModel(name='StockCount'),
    ]
