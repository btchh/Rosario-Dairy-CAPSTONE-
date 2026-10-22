from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import Table
from rest_framework.test import APIClient

from inventory.models import Category, Product, ProductBatch, StockAdjustment
from reporting.pdf import Report
from reporting.serializers import InventoryReportSerializer
from reporting.services import inventory_status
from sales.models import Customer, Transaction, TransactionItem


class InventoryLedgerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username='ledgeradmin', email='ledger@example.test', role='admin',
        )
        self.product = Product.objects.create(
            name='Fresh milk', variant='1000ml', unit='piece', shelf_life=7,
            category=Category.objects.create(name='Dairy'),
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='LEDGER-1', initial_quantity=20,
            remaining_quantity=14, expiration_date=timezone.localdate() + timedelta(days=7),
        )
        ProductBatch.objects.filter(pk=self.batch.pk).update(created_at=timezone.now() - timedelta(days=1))
        self.customer = Customer.objects.create(name='School outlet', created_by=self.user)
        self.sale(3, self.customer)
        self.sale(2)
        StockAdjustment.objects.create(
            product_batch=self.batch, adjustment_type='free_taste', quantity=1,
            unit_cost=10, adjusted_by=self.user, reason='School samples',
        )

    def sale(self, quantity, customer=None, **kwargs):
        sale = Transaction.objects.create(handled_by=self.user, customer=customer, **kwargs)
        TransactionItem.objects.create(transaction=sale, product_batch=self.batch, quantity=quantity, unit_price=10)
        return sale

    def ledger(self, staff=False):
        return inventory_status(staff)['ledger']

    def test_opening_receipts_allocations_and_signed_adjustments_reconcile(self):
        ProductBatch.objects.create(
            product=self.product, batch_number='LEDGER-2', initial_quantity=8,
            remaining_quantity=8, expiration_date=timezone.localdate() + timedelta(days=7),
        )
        StockAdjustment.objects.create(
            product_batch=self.batch, adjustment_type='correction', quantity=-2,
            unit_cost=10, adjusted_by=self.user, reason='Recount',
        )
        ProductBatch.objects.filter(pk=self.batch.pk).update(remaining_quantity=16)
        row = self.ledger()['rows'][0]
        self.assertEqual((row['beginning'], row['received'], row['total'], row['ending']), (20, 8, 28, 24))
        self.assertEqual(row['outlet'], 2)
        self.assertEqual(row['allocations'], {f'customer-{self.customer.pk}': 3})
        self.assertEqual(row['adjustments'], -1)
        self.assertIn('School samples', row['remarks'])
        self.assertEqual(row['total'] - row['outlet'] - sum(row['allocations'].values()) - row['adjustments'], row['ending'])

    def test_uses_encoding_date_and_excludes_voids_and_reference_batches(self):
        backdated = self.sale(4, self.customer, encoded_at=timezone.now())
        Transaction.objects.filter(pk=backdated.pk).update(created_at=timezone.now() - timedelta(days=10))
        old_entry = self.sale(7, self.customer, encoded_at=timezone.now() - timedelta(days=1))
        Transaction.objects.filter(pk=old_entry.pk).update(created_at=timezone.now())
        self.sale(100, self.customer, is_voided=True)
        historical = ProductBatch.objects.create(
            product=self.product, batch_number='HISTORY', initial_quantity=999,
            remaining_quantity=999, is_historical_reference=True,
            expiration_date=timezone.localdate() + timedelta(days=7),
        )
        sale = Transaction.objects.create(handled_by=self.user, customer=self.customer)
        TransactionItem.objects.create(transaction=sale, product_batch=historical, quantity=100, unit_price=10)
        row = self.ledger()['rows'][0]
        self.assertEqual(row['allocations'][f'customer-{self.customer.pk}'], 7)
        self.assertEqual(row['ending'], 14)
        self.assertEqual(row['received'], 0)

    def test_expired_physical_balance_is_distinct_from_sellable_stock(self):
        ProductBatch.objects.filter(pk=self.batch.pk).update(expiration_date=timezone.localdate() - timedelta(days=1))
        report = inventory_status()
        self.assertEqual(report['ledger']['rows'][0]['ending'], 14)
        self.assertEqual(report['items'][0]['quantity'], 0)
        self.assertIn('expired on hand', report['ledger']['rows'][0]['remarks'])

    def test_staff_cannot_see_hidden_products_or_mixed_invoice_buyers(self):
        hidden = Product.objects.create(
            name='Hidden', unit='piece', shelf_life=7,
            category=Category.objects.create(name='Private', is_visible_to_staff=False),
        )
        hidden_batch = ProductBatch.objects.create(
            product=hidden, batch_number='HIDDEN', initial_quantity=10, remaining_quantity=9,
            expiration_date=timezone.localdate() + timedelta(days=7),
        )
        customer = Customer.objects.create(name='Private buyer', created_by=self.user)
        mixed = self.sale(1, customer)
        TransactionItem.objects.create(transaction=mixed, product_batch=hidden_batch, quantity=1, unit_price=10)
        ledger = self.ledger(staff=True)
        self.assertEqual(len(ledger['rows']), 1)
        self.assertNotIn('Private buyer', str(ledger['columns']))
        self.assertEqual(ledger['rows'][0]['allocations']['restricted'], 1)

    def test_inventory_preview_and_download_include_ledger(self):
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.get('/api/reports/preview/?type=inventory')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['ledger']['rows'][0]['beginning'], '20.00')
        response = client.get('/api/reports/export-pdf/?type=inventory')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))

    def test_many_customer_columns_continue_on_landscape_sheets(self):
        for i in range(10):
            self.sale(Decimal('0.5'), Customer.objects.create(name=f'Outlet {i}', created_by=self.user))
        data = InventoryReportSerializer(inventory_status()).data
        report = Report('inventory', data)
        report.inventory_ledger()
        tables = [part for part in report.story if isinstance(part, Table)]
        self.assertEqual(len(tables), 2)
        self.assertEqual(report.page_size, landscape(A4))
        self.assertTrue(all(table.repeatRows == 2 for table in tables))
        for column in data['ledger']['columns']:
            self.assertTrue(any(column['name'] in str(table._cellvalues) for table in tables))
        self.assertTrue(Report('inventory', data).render().read().startswith(b'%PDF'))

    def test_empty_ledger_still_exports(self):
        Product.objects.filter(pk=self.product.pk).update(is_active=False)
        data = InventoryReportSerializer(inventory_status()).data
        self.assertEqual(data['ledger']['rows'], [])
        self.assertTrue(Report('inventory', data).render().read().startswith(b'%PDF'))
