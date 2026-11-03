"""Year-scoped SI numbering, paper provenance, retries and PostgreSQL races."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone as dt_timezone
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from inventory.models import Category, Product, ProductBatch
from sales.models import Customer, InvoiceSequence, Order, SaleSubmission, Transaction
from sales.services import SalesService


class InvoiceFixture:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.clock = patch('django.utils.timezone.now', return_value=datetime(
            2026, 11, 3, 4, tzinfo=dt_timezone.utc))
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.user = get_user_model().objects.create_user(
            username='invoice-staff', email='invoice-staff@example.test', role='staff')
        self.other = get_user_model().objects.create_user(
            username='invoice-admin', email='invoice-admin@example.test', role='admin')
        category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(name='Milk', category=category, unit='L', shelf_life=7)
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='INVOICE-STOCK', initial_quantity=100,
            remaining_quantity=100, date_received=date(2020, 1, 1),
            expiration_date=date(2035, 1, 1))
        self.customer = Customer.objects.create(name='Invoice customer', created_by=self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def payload(self, **overrides):
        return {
            'request_id': str(uuid4()), 'transaction_date': '2026-11-03',
            'customer_id': self.customer.pk, 'payment_method': 'online',
            'payment_status': 'paid',
            'items': [{'product_id': self.product.pk, 'quantity': '1', 'unit_price': '10'}],
            **overrides,
        }

    def post(self, endpoint='encoding', **overrides):
        return self.client.post(f'/sales/{endpoint}/', self.payload(**overrides), format='json')

    def sale(self, response):
        self.assertIn(response.status_code, (200, 201), response.data)
        return response.data.get('transaction', response.data)

    def legacy(self, si, year=2026, **overrides):
        sale = Transaction.objects.create(
            handled_by=self.user, source_invoice_number=si, **overrides)
        Transaction.objects.filter(pk=sale.pk).update(created_at=timezone.make_aware(
            datetime(year, 6, 1, 12)))
        sale.refresh_from_db()
        return sale


@override_settings(TIME_ZONE='Asia/Manila')
class InvoiceNumberTests(InvoiceFixture, TestCase):
    def test_pos_order_and_blank_encoding_share_counter_and_existing_api_fields(self):
        for index, endpoint in enumerate(('checkout', 'orders', 'encoding'), 1):
            response = self.post(endpoint)
            sale = self.sale(response)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(sale['transaction_number'], f'SI{index:04d}')
            self.assertEqual(sale['source_invoice_number'], f'SI{index:04d}')
            stored = Transaction.objects.get(pk=sale['id'])
            self.assertEqual(stored.invoice_year, 2026)
            self.assertEqual(stored.invoice_number_source, 'automatic')
            self.assertNotIn('invoice_number_source', sale)
        self.assertEqual(InvoiceSequence.objects.get(year=2026).last_value, 3)

    def test_new_year_restarts_using_manila_midnight(self):
        for instant, year in ((datetime(2026, 12, 31, 15, 59, tzinfo=dt_timezone.utc), 2026),
                              (datetime(2026, 12, 31, 16, 0, tzinfo=dt_timezone.utc), 2027)):
            with patch('django.utils.timezone.now', return_value=instant):
                sale = self.sale(self.post('checkout'))
            self.assertEqual(sale['transaction_number'], 'SI0001')
            stored = Transaction.objects.get(pk=sale['id'])
            self.assertEqual(stored.invoice_year, year)
            self.assertEqual(timezone.localtime(stored.created_at).year, year)
        self.assertEqual(list(InvoiceSequence.objects.order_by('year').values_list(
            'year', 'last_value')), [(2026, 1), (2027, 1)])

    def test_checkout_spanning_midnight_keeps_allocation_year_and_sales_date(self):
        import sales.services.checkout_service as checkout
        original = checkout.assign_invoice

        def assign_and_cross_midnight(*args):
            result = original(*args)
            self.now.return_value = datetime(2026, 12, 31, 16, 0, tzinfo=dt_timezone.utc)
            return result

        self.now.return_value = datetime(2026, 12, 31, 15, 59, tzinfo=dt_timezone.utc)
        with patch.object(checkout, 'assign_invoice', side_effect=assign_and_cross_midnight):
            sale = self.sale(self.post('checkout'))
        stored = Transaction.objects.get(pk=sale['id'])
        self.assertEqual(stored.invoice_year, 2026)
        self.assertEqual(timezone.localtime(stored.created_at).year, 2026)

    def test_blank_backdated_encoding_uses_sales_year_and_keeps_entry_time(self):
        old = self.legacy('SI0608', year=2025)
        before = Transaction.objects.filter(pk=old.pk).values().get()
        for customer in (None, self.customer.pk):
            sale = self.sale(self.post(transaction_date='2025-12-31', si_number='', customer_id=customer))
            stored = Transaction.objects.get(pk=sale['id'])
            self.assertEqual(stored.invoice_year, 2025)
            self.assertEqual(stored.invoice_number_source, 'automatic')
            self.assertEqual(timezone.localtime(stored.created_at).date(), date(2025, 12, 31))
            self.assertEqual(timezone.localtime(stored.encoded_at).year, 2026)
        self.assertEqual(list(Transaction.objects.exclude(pk=old.pk).order_by('pk').values_list(
            'source_invoice_number', flat=True)), ['SI0609', 'SI0610'])
        self.assertEqual(Transaction.objects.filter(pk=old.pk).values().get(), before)
        self.assertFalse(InvoiceSequence.objects.filter(year=2026).exists())

    def test_order_crossing_new_year_keeps_order_and_linked_invoice_in_same_year(self):
        from sales.services import order_service
        original = order_service.checkout_service.checkout

        def checkout_after_midnight(*args, **kwargs):
            self.now.return_value = datetime(2026, 12, 31, 16, 0, tzinfo=dt_timezone.utc)
            return original(*args, **kwargs)

        self.now.return_value = datetime(2026, 12, 31, 15, 59, tzinfo=dt_timezone.utc)
        with patch.object(order_service.checkout_service, 'checkout', side_effect=checkout_after_midnight):
            sale = self.sale(self.post('orders'))
        stored = Transaction.objects.get(pk=sale['id'])
        order = Order.objects.get(transaction=stored)
        self.assertEqual(stored.invoice_year, 2027)
        self.assertEqual(order.created_at, stored.created_at)
        self.assertEqual(timezone.localtime(order.created_at).year, 2027)

    def test_whitespace_only_si_falls_back_to_automatic_number(self):
        self.assertEqual(self.sale(self.post(si_number='   '))['transaction_number'], 'SI0001')

    def test_manual_si_is_preserved_and_does_not_advance_existing_counter(self):
        InvoiceSequence.objects.create(year=2026, last_value=4)
        sale = self.sale(self.post(si_number='si-Paper-008', transaction_date='2026-01-02'))
        stored = Transaction.objects.get(pk=sale['id'])
        self.assertEqual(sale['transaction_number'], 'si-Paper-008')
        self.assertEqual(stored.invoice_number_source, 'manual')
        self.assertEqual(stored.invoice_year, 2026)
        self.assertEqual(InvoiceSequence.objects.get(year=2026).last_value, 4)

    def test_paper_si_can_overlap_generated_pos_or_generated_encoding(self):
        for endpoint in ('checkout', 'orders', 'encoding'):
            generated = self.sale(self.post(endpoint))
            paper = self.sale(self.post(si_number=generated['transaction_number']))
            self.assertNotEqual(generated['id'], paper['id'])
            self.assertEqual(generated['transaction_number'], paper['transaction_number'])
            duplicate = self.post(si_number=generated['transaction_number'])
            self.assertEqual(duplicate.status_code, 409, duplicate.data)
        self.assertEqual(Transaction.objects.count(), 6)

    def test_duplicate_paper_si_checks_sales_year_across_dates_users_and_customers(self):
        self.sale(self.post(si_number='SI0480', transaction_date='2026-01-02'))
        self.client.force_authenticate(self.other)
        conflict = self.post(si_number='si0480', transaction_date='2026-10-10', customer_id=None)
        self.assertEqual(conflict.status_code, 409, conflict.data)
        self.assertIn('already recorded for 2026', conflict.data['error'])
        self.assertEqual(set(conflict.data), {'error'})
        self.assertEqual(Transaction.objects.count(), 1)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 99)
        old_year = self.post(si_number='SI0480', transaction_date='2025-12-31')
        self.assertEqual(old_year.status_code, 201, old_year.data)

    def test_legacy_duplicates_are_preserved_and_prevent_new_paper_duplicate(self):
        old = [self.legacy('SI0480'), self.legacy('SI0480')]
        before = list(Transaction.objects.order_by('pk').values())
        response = self.post(si_number='SI0480')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(list(Transaction.objects.order_by('pk').values()), before)
        self.assertEqual(len(old), 2)

    def test_auto_skips_paper_imported_and_voided_numbers_only_in_same_year(self):
        InvoiceSequence.objects.create(year=2026, last_value=9)
        self.legacy(' SI0010 ', source_reference='original:10')
        self.legacy('si0011', is_voided=True)
        self.legacy('SI0012', year=2025)
        sale = self.sale(self.post('checkout'))
        self.assertEqual(sale['transaction_number'], 'SI0012')

    def test_seed_uses_latest_year_series_instead_of_max_or_another_year(self):
        self.legacy('SI037806')
        latest = self.legacy('SI0608')
        Transaction.objects.filter(pk=latest.pk).update(created_at=timezone.now())
        self.legacy('SI99999', year=2025)
        self.assertEqual(self.sale(self.post('checkout'))['transaction_number'], 'SI0609')

    def test_first_manual_entry_does_not_reserve_a_gap_or_push_counter_ahead(self):
        self.sale(self.post(si_number='SI0010'))
        self.assertEqual(self.sale(self.post('checkout'))['transaction_number'], 'SI0001')

    def test_counter_keeps_advancing_after_cancellation(self):
        first = self.sale(self.post('checkout'))
        from sales.services.order_service import void_transaction
        void_transaction(first['id'], voided_by=self.other, reason='Cancelled')
        second = self.sale(self.post('checkout'))
        self.assertEqual(second['transaction_number'], 'SI0002')

    def test_number_expands_past_four_digits(self):
        InvoiceSequence.objects.create(year=2026, last_value=9999)
        self.assertEqual(self.sale(self.post('checkout'))['transaction_number'], 'SI10000')

    def test_retry_preserves_same_number_and_counter_for_every_entry_path(self):
        for endpoint, number in (('checkout', None), ('orders', None),
                                 ('encoding', ''), ('encoding', 'SI-PAPER')):
            payload = self.payload(**({'si_number': number} if number is not None else {}))
            first = self.client.post(f'/sales/{endpoint}/', payload, format='json')
            saved = list(InvoiceSequence.objects.values())
            retry = self.client.post(f'/sales/{endpoint}/', payload, format='json')
            self.assertEqual((first.status_code, retry.status_code), (201, 200), retry.data)
            self.assertEqual(self.sale(first)['id'], self.sale(retry)['id'])
            self.assertEqual(self.sale(first)['transaction_number'], self.sale(retry)['transaction_number'])
            self.assertEqual(list(InvoiceSequence.objects.values()), saved)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 96)

    def test_changed_retry_is_conflict_without_allocating_a_number(self):
        for endpoint in ('checkout', 'orders', 'encoding'):
            payload = self.payload()
            self.sale(self.client.post(f'/sales/{endpoint}/', payload, format='json'))
            before = list(InvoiceSequence.objects.values())
            payload['items'][0]['quantity'] = '2'
            response = self.client.post(f'/sales/{endpoint}/', payload, format='json')
            self.assertEqual(response.status_code, 409, response.data)
            self.assertEqual(list(InvoiceSequence.objects.values()), before)

    def test_insufficient_stock_rolls_back_number_and_all_sale_records(self):
        for endpoint in ('checkout', 'orders', 'encoding'):
            response = self.post(endpoint, items=[
                {'product_id': self.product.pk, 'quantity': '101', 'unit_price': '10'}])
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(InvoiceSequence.objects.exists())
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Order.objects.exists())
        self.assertFalse(SaleSubmission.objects.exists())
        self.assertEqual(self.sale(self.post('checkout'))['transaction_number'], 'SI0001')

    def test_numbering_parameters_cannot_be_spoofed_through_api(self):
        for endpoint in ('checkout', 'orders', 'encoding'):
            sale = self.sale(self.post(endpoint, invoice_year=1900,
                                      invoice_number_source='manual', source_invoice_number='SI9999'))
            stored = Transaction.objects.get(pk=sale['id'])
            self.assertEqual(stored.invoice_year, 2026)
            self.assertEqual(stored.invoice_number_source, 'automatic')
            self.assertNotEqual(stored.source_invoice_number, 'SI9999')

    def test_database_rejects_duplicate_new_numbers_within_year_and_source(self):
        for source in ('automatic', 'manual'):
            with transaction.atomic():
                self.legacy('SI0001', invoice_year=2026, invoice_number_source=source)
            with self.assertRaises(IntegrityError), transaction.atomic():
                self.legacy('si0001', invoice_year=2026, invoice_number_source=source)


@skipUnless(connection.vendor == 'postgresql', 'SI allocation requires PostgreSQL row locks.')
@override_settings(TIME_ZONE='Asia/Manila')
class ConcurrentInvoiceNumberTests(InvoiceFixture, TransactionTestCase):
    def race(self, submissions):
        barrier = Barrier(len(submissions))

        def submit(spec):
            endpoint, payload = spec
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(self.user)
                barrier.wait(timeout=10)
                response = client.post(f'/sales/{endpoint}/', payload, format='json')
                return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(submissions)) as pool:
            futures = [pool.submit(submit, spec) for spec in submissions]
            return [future.result(timeout=30) for future in futures]

    def test_first_sales_of_year_race_across_all_three_paths(self):
        results = self.race([(endpoint, self.payload())
                             for endpoint in ('checkout', 'orders', 'encoding')])
        self.assertEqual([code for code, _ in results], [201, 201, 201], results)
        sales = [data.get('transaction', data) for _, data in results]
        self.assertEqual({sale['transaction_number'] for sale in sales}, {'SI0001', 'SI0002', 'SI0003'})
        self.assertEqual(InvoiceSequence.objects.get(year=2026).last_value, 3)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 97)

    def test_concurrent_paper_duplicates_with_new_request_ids_commit_once(self):
        results = self.race([('encoding', self.payload(si_number='SI-PAPER')) for _ in range(2)])
        self.assertEqual(sorted(code for code, _ in results), [201, 409], results)
        self.assertEqual(Transaction.objects.count(), 1)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 99)

    def test_same_request_using_last_stock_allocates_once_for_each_path(self):
        for endpoint in ('checkout', 'orders', 'encoding'):
            ProductBatch.objects.filter(pk=self.batch.pk).update(remaining_quantity=1, status='available')
            payload = self.payload()
            results = self.race([(endpoint, payload), (endpoint, payload)])
            self.assertEqual(sorted(code for code, _ in results), [200, 201], results)
            sales = [data.get('transaction', data) for _, data in results]
            self.assertEqual(sales[0]['id'], sales[1]['id'])
        self.assertEqual(Transaction.objects.count(), 3)
        self.assertEqual(InvoiceSequence.objects.get(year=2026).last_value, 3)

    def test_manual_and_automatic_race_retains_paper_number_and_unique_auto_numbers(self):
        results = self.race([('encoding', self.payload(si_number='SI0001')),
                             ('checkout', self.payload())])
        self.assertEqual([code for code, _ in results], [201, 201], results)
        self.assertEqual(Transaction.objects.filter(invoice_number_source='manual').get().source_invoice_number, 'SI0001')
        self.assertIn(Transaction.objects.filter(invoice_number_source='automatic').get().source_invoice_number,
                      ('SI0001', 'SI0002'))
        next_sale = self.sale(self.post('checkout'))
        self.assertNotEqual(next_sale['transaction_number'], 'SI0001')
