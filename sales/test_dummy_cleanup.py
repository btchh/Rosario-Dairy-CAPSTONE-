import gzip
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase

from forecasting.models import ForecastExperiment, GraphForecastRun, GraphPrediction
from inventory.models import Category, Product, ProductBatch
from sales.models import Customer, Order, OrderItem, Transaction, TransactionItem
from sales.management.commands.remove_dummy_data import DEMO_REFERENCE, fingerprint


class DummyCleanupTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='cleanup-admin', email='cleanup@example.test', role='admin')
        self.customer = Customer.objects.create(name='Shared real customer', created_by=self.owner)
        category = Category.objects.create(name='Cleanup dairy')
        self.product = Product.objects.create(name='FML', category=category, unit='L', shelf_life=7)
        self.batch = ProductBatch.objects.create(product=self.product, batch_number='SHARED-HISTORY',
            initial_quantity=10, remaining_quantity=10, expiration_date='2030-01-01', is_historical_reference=True)
        self.dummy = self.sale('2026:000001')
        self.real = [self.sale(ref) for ref in ('2026:REAL:1', '2026:000001-extra', None)]
        self.demo = GraphForecastRun.objects.create(engine_version='old', scope='demo2026', period='monthly',
            as_of='2026-10-26', source_revision='demo', findings={})
        self.real_graph = GraphForecastRun.objects.create(engine_version='old', scope='real', period='monthly',
            as_of='2026-10-26', source_revision='real', findings={})
        for run in (self.demo, self.real_graph):
            GraphPrediction.objects.create(run=run, view='regular', target='2026-11-01', value=10)
        ForecastExperiment.objects.create(version='archived', source_revision='synthetic', findings={})

    def sale(self, reference):
        sale = Transaction.objects.create(handled_by=self.owner, customer=self.customer,
            source_reference=reference, total_amount=100)
        TransactionItem.objects.create(transaction=sale, product_batch=self.batch,
            quantity=1, unit_price=100, total_amount=100)
        order = Order.objects.create(transaction=sale, customer=self.customer, handled_by=self.owner)
        OrderItem.objects.create(order=order, product=self.product, quantity=1, unit_price=100, subtotal=100)
        return sale

    def test_preview_does_not_delete_or_create_backup(self):
        output = StringIO()
        call_command('remove_dummy_data', stdout=output)
        self.assertFalse(json.loads(output.getvalue())['applied'])
        self.assertEqual(Transaction.objects.count(), 4)
        self.assertEqual(GraphForecastRun.objects.count(), 2)

    def test_apply_removes_only_generated_records_and_verifies_full_backup(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)/'recovery'
            call_command('remove_dummy_data', apply=True, output=folder, stdout=StringIO())
            manifest = json.loads((folder/'manifest.json').read_text())
            self.assertTrue(manifest['completed'])
            self.assertTrue(manifest['protected_verified'])
            with gzip.open(folder/'database-before.json.gz', 'rt', encoding='utf-8') as source:
                backup = json.load(source)
            self.assertTrue(any(r['model'] == 'sales.transaction' and r['pk'] == self.dummy.pk for r in backup))
        self.assertFalse(Transaction.objects.filter(source_reference__regex=DEMO_REFERENCE).exists())
        self.assertEqual(set(Transaction.objects.values_list('pk', flat=True)), {s.pk for s in self.real})
        self.assertEqual(Order.objects.count(), 3)
        self.assertEqual(OrderItem.objects.count(), 3)
        self.assertEqual(TransactionItem.objects.count(), 3)
        self.assertEqual(GraphForecastRun.objects.get().pk, self.real_graph.pk)
        self.assertEqual(GraphPrediction.objects.get().run_id, self.real_graph.pk)
        self.assertFalse(ForecastExperiment.objects.exists())
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 10)
        self.assertTrue(Customer.objects.filter(pk=self.customer.pk).exists())

    @skipUnless(os.name == 'posix', 'POSIX modes do not verify Windows ACL protection.')
    def test_backup_permissions_are_private_on_posix(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)/'recovery'
            call_command('remove_dummy_data', apply=True, output=folder, stdout=StringIO())
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            for filename in ('database-before.json.gz', 'manifest.json'):
                self.assertEqual((folder/filename).stat().st_mode & 0o777, 0o600)

    def test_backup_failure_leaves_sales_and_demo_forecasts_untouched(self):
        with TemporaryDirectory() as directory, \
                patch('sales.management.commands.remove_dummy_data.call_command', side_effect=OSError('backup failed')):
            with self.assertRaisesMessage(CommandError, 'backup failed'):
                call_command('remove_dummy_data', apply=True, output=Path(directory)/'recovery', stdout=StringIO())
        self.assertEqual(Transaction.objects.count(), 4)
        self.assertEqual(GraphForecastRun.objects.count(), 2)

    def test_preservation_failure_rolls_back_all_deletions(self):
        def checked(query):
            if not Transaction.objects.filter(pk=self.dummy.pk).exists():
                return 'unexpected unrelated change'
            return fingerprint(query)
        with TemporaryDirectory() as directory, \
                patch('sales.management.commands.remove_dummy_data.fingerprint', side_effect=checked):
            with self.assertRaisesMessage(CommandError, 'Removal rolled back'):
                call_command('remove_dummy_data', apply=True, output=Path(directory)/'recovery', stdout=StringIO())
        self.assertEqual(Transaction.objects.count(), 4)
        self.assertEqual(GraphForecastRun.objects.count(), 2)
        self.assertEqual(GraphPrediction.objects.count(), 2)

    def test_live_stock_requires_reconciliation_instead_of_silent_deletion(self):
        self.batch.is_historical_reference = False
        self.batch.save()
        with TemporaryDirectory() as directory:
            with self.assertRaisesMessage(CommandError, 'live stock'):
                call_command('remove_dummy_data', apply=True, output=Path(directory)/'recovery', stdout=StringIO())
        self.assertEqual(Transaction.objects.count(), 4)
