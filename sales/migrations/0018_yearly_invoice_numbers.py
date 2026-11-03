from django.db import migrations, models
from django.db.models.functions import Trim, Upper


class Migration(migrations.Migration):
    dependencies = [
        ('sales', '0017_transaction_void_reason_transaction_voided_at_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='InvoiceSequence',
            fields=[
                ('year', models.PositiveSmallIntegerField(primary_key=True, serialize=False)),
                ('last_value', models.PositiveBigIntegerField(default=0)),
            ],
        ),
        migrations.AddField(
            model_name='transaction', name='invoice_year',
            field=models.PositiveSmallIntegerField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name='transaction', name='invoice_number_source',
            field=models.CharField(blank=True, default='', editable=False, max_length=10,
                                   choices=[('automatic', 'Automatic'), ('manual', 'Paper invoice')]),
        ),
        migrations.AddConstraint(
            model_name='transaction',
            constraint=models.UniqueConstraint(
                Upper(Trim('source_invoice_number')), 'invoice_year', 'invoice_number_source',
                condition=models.Q(invoice_number_source__in=['automatic', 'manual']),
                name='sales_invoice_year_source_unique',
            ),
        ),
    ]
