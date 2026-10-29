"""Batch extraction must preserve ledger revisions and exclusive local dates."""
from datetime import date, datetime, timedelta
import json
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from forecasting.data import load_snapshot, load_snapshots
from inventory.models import Category, Product, ProductBatch
from sales.importers.refresh_annual_sales import product_key
from sales.models import Customer, Transaction, TransactionItem


@override_settings(CACHES={'default': {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'batch-snapshot-tests',
}})
class SnapshotBatchTests(TestCase):
    def setUp(self):
        self.enterContext(timezone.override('Asia/Manila'))
        policy = self.enterContext(patch('forecasting.data.POLICY_PATH'))
        policy.read_text.return_value = json.dumps({'products': {
            product_key('FML'): {'decision': 'dairy'},
            product_key('Water'): {'decision': 'non_dairy'},
        }})
        self.policy = policy
        self.user = get_user_model().objects.create_user(username='snapshot-audit', role='admin')
        self.customer = Customer.objects.create(name='Snapshot customer', created_by=self.user)
        category = Category.objects.create(name='Snapshot dairy')
        product = Product.objects.create(name='FML', unit='L', shelf_life=7, category=category)
        self.batch = ProductBatch.objects.create(
            product=product, batch_number='SNAPSHOT-AUDIT', initial_quantity=10,
            remaining_quantity=10, expiration_date=date(2030, 1, 1))
        self.early = self.sale(datetime(2025, 12, 31, 23, 59), '100')
        self.boundary = self.sale(datetime(2026, 1, 1), '200')
        self.demo = self.sale(datetime(2026, 1, 2, 12), '300', source_reference='2026:000001')
        self.non_dairy = self.sale(datetime(2026, 1, 2, 13), '400', label='Water')
        self.sale(datetime(2026, 1, 3, 12), '500', source_sales_segment='feeding')
        self.sale(datetime(2026, 1, 3, 13), '600', is_voided=True)
        self.cutoffs = [date(2026, 1, 1) + timedelta(days=n) for n in range(4)]

    def sale(self, stamp, amount, *, label='FML', **extra):
        sale = Transaction.objects.create(
            handled_by=self.user, customer=self.customer, total_amount=amount, **extra)
        Transaction.objects.filter(pk=sale.pk).update(created_at=stamp.replace(tzinfo=ZoneInfo('Asia/Manila')))
        TransactionItem.objects.create(transaction=sale, product_batch=self.batch,
            product_name_snapshot=label, quantity=1, unit_price=amount, total_amount=amount)
        return sale

    def test_batch_preserves_independent_cutoff_contents_exclusions_and_revisions(self):
        expected = {day: load_snapshot(day) for day in self.cutoffs}
        actual = load_snapshots(self.cutoffs)
        self.assertEqual(actual, expected)
        self.assertEqual([sale.id for sale in actual[self.cutoffs[0]].sales], [self.early.pk])
        self.assertEqual([sale.id for sale in actual[self.cutoffs[1]].sales], [self.early.pk, self.boundary.pk])
        self.assertEqual(actual[self.cutoffs[1]].excluded_demo_count, 0)
        self.assertEqual(actual[self.cutoffs[2]].excluded_demo_count, 1)
        self.assertEqual(actual[self.cutoffs[1]].excluded_non_dairy_count, 0)
        self.assertEqual(actual[self.cutoffs[2]].excluded_non_dairy_count, 1)
        self.assertEqual(len(actual[self.cutoffs[3]].sales), 3)

    def test_multiple_cutoffs_share_one_receipt_and_one_line_query(self):
        with self.assertNumQueries(2):
            snapshots = load_snapshots(self.cutoffs + self.cutoffs)
        self.assertEqual(len(snapshots), 4)
        self.policy.read_text.assert_called_once_with()

    def test_next_extraction_detects_bulk_edits_without_invalidation_signals(self):
        before = load_snapshots(self.cutoffs)
        Transaction.objects.filter(pk=self.boundary.pk).update(total_amount='250')
        after = load_snapshots(self.cutoffs)
        self.assertEqual(before[self.cutoffs[0]], after[self.cutoffs[0]])
        self.assertNotEqual(before[self.cutoffs[1]].revision, after[self.cutoffs[1]].revision)
        Customer.objects.filter(pk=self.customer.pk).update(name='Corrected customer')
        renamed = load_snapshots(self.cutoffs)
        self.assertNotEqual(after[self.cutoffs[1]].revision, renamed[self.cutoffs[1]].revision)
        TransactionItem.objects.filter(transaction=self.boundary).update(product_name_snapshot='Water')
        corrected = load_snapshots(self.cutoffs)
        self.assertNotEqual(renamed[self.cutoffs[1]].revision, corrected[self.cutoffs[1]].revision)
        self.assertEqual(corrected[self.cutoffs[1]].excluded_non_dairy_count, 1)

    def test_empty_and_invalid_cutoffs_do_not_query_the_database(self):
        with self.assertNumQueries(0):
            self.assertEqual(load_snapshots([]), {})
            for value in (None, '2026-01-01', datetime(2026, 1, 1), []):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    load_snapshots([value])
