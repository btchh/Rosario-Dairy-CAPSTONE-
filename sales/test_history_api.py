"""Paged history and full-result totals share filters and access boundaries."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from config.test_support import APITestCase
from inventory.models import Category, Product, ProductBatch
from sales.models import Customer, Order, OrderItem, Transaction, TransactionItem


class HistoryAPITests(APITestCase):
    def setUp(self):
        cache.clear()
        users = get_user_model()
        self.admin = users.objects.create_user(username='history-admin', email='history-admin@example.test', role='admin')
        self.staff = users.objects.create_user(username='history-staff', email='history-staff@example.test', role='staff')
        self.customer = Customer.objects.create(name='History Buyer', created_by=self.admin)
        self.client.force_authenticate(self.admin)

    def sale(self, amount='10.10', *, order=False, order_status='fulfilled', **extra):
        sale = Transaction.objects.create(
            handled_by=extra.pop('handled_by', self.admin), customer=self.customer,
            subtotal=Decimal(amount), total_amount=Decimal(amount), **extra)
        if not order:
            return sale
        return Order.objects.create(customer=self.customer, handled_by=sale.handled_by,
                                    transaction=sale, status=order_status)

    def get(self, resource, suffix='', **params):
        response = self.client.get(f'/sales/{resource}/{suffix}', params)
        self.assertEqual(response.status_code, 200, response.data)
        return response.json()

    def batch(self, name, **flags):
        category = Category.objects.create(name=name, **flags)
        product = Product.objects.create(name=name, category=category, unit='L', shelf_life=7)
        return ProductBatch.objects.create(product=product, batch_number=name,
            initial_quantity=100, remaining_quantity=100,
            expiration_date=timezone.localdate() + timedelta(days=7))

    def attach(self, sale, batch):
        TransactionItem.objects.create(transaction=sale, product_batch=batch, quantity=1, unit_price=5)

    def test_empty_summaries_return_exact_decimal_zero_and_integer_counts(self):
        self.assertEqual(self.get('transactions', 'summary/'), {
            'transaction_count': 0, 'total_revenue': '0.00', 'paid_count': 0, 'paid_amount': '0.00',
            'pending_count': 0, 'pending_amount': '0.00', 'unknown_count': 0, 'unknown_amount': '0.00'})
        self.assertEqual(self.get('orders', 'summary/'), {
            'order_count': 0, 'total_revenue': '0.00', 'fulfilled_count': 0,
            'cancelled_count': 0, 'pending_payment_count': 0})

    def test_summary_preserves_recorded_totals_and_does_not_deduplicate_equal_amounts(self):
        sale = self.sale('10.10', payment_status='paid')
        sale.subtotal = Decimal('12.10')
        sale.discount_amount = Decimal('2.00')
        sale.save()
        self.sale('10.10', payment_status='paid')
        self.sale('0.20', payment_status='pending', encoded_at=timezone.now())
        self.sale('5.25', payment_status='unknown', source_reference='HISTORY-IMPORT')
        self.sale('999.00', payment_status='paid', is_voided=True)
        before = list(Transaction.objects.order_by('pk').values())
        data = self.get('transactions', 'summary/', page_size=1, page=999)
        self.assertEqual(data, {'transaction_count': 4, 'total_revenue': '25.65',
            'paid_count': 2, 'paid_amount': '20.20', 'pending_count': 1, 'pending_amount': '0.20',
            'unknown_count': 1, 'unknown_amount': '5.25'})
        self.assertEqual(self.get('transactions', 'summary/', include_voided='true')['total_revenue'], '1024.65')
        self.assertEqual(before, list(Transaction.objects.order_by('pk').values()))

    def test_aggregate_can_exceed_one_receipts_decimal_precision(self):
        self.sale('999999999999999999.99', order=True)
        self.sale('999999999999999999.99', order=True)
        for resource in ('transactions', 'orders'):
            self.assertEqual(self.get(resource, 'summary/')['total_revenue'], '1999999999999999999.98')

    def test_list_and_summary_apply_identical_combined_filters(self):
        selected = self.sale('23.45', order=True, handled_by=self.staff,
            source_invoice_number='SI-SPECIAL', payment_method='online', payment_status='pending')
        self.sale('90.00', order=True, payment_method='cash', payment_status='paid')
        today = timezone.localdate().isoformat()
        params = {'start_date': today, 'end_date': today, 'handled_by': self.staff.pk,
                  'customer_id': self.customer.pk, 'payment_method': 'online',
                  'payment_status': 'pending', 'search': 'special'}
        for resource, expected, count in (('transactions', selected.transaction_id, 'transaction_count'),
                                           ('orders', selected.pk, 'order_count')):
            data = self.get(resource, **params)
            self.assertEqual(data['count'], 1)
            self.assertEqual(data['results'][0]['id'], expected)
            summary = self.get(resource, 'summary/', **params)
            self.assertEqual(summary[count], 1)
            self.assertEqual(summary['total_revenue'], '23.45')

    def test_search_is_case_insensitive_and_matches_customer_handler_and_invoice(self):
        selected = self.sale(order=True, handled_by=self.staff, source_invoice_number='SI-Search-42')
        for resource in ('transactions', 'orders'):
            for term in ('buyer', 'HISTORY-STAFF', 'search-42'):
                self.assertEqual(self.get(resource, search=term)['count'], 1)
            self.assertEqual(self.get(resource, search="' OR 1=1 --")['count'], 0)
        self.assertEqual(self.get('orders', search=str(selected.pk))['results'][0]['id'], selected.pk)

    def test_manila_day_bounds_include_both_edges_and_exclude_adjacent_days(self):
        day = timezone.localdate()
        begin = timezone.make_aware(datetime.combine(day, time.min))
        end = timezone.make_aware(datetime.combine(day, time.max))
        for point in (begin - timedelta(microseconds=1), begin, end, end + timedelta(microseconds=1)):
            order = self.sale(order=True)
            Transaction.objects.filter(pk=order.transaction_id).update(created_at=point)
        for resource in ('transactions', 'orders'):
            params = {'start_date': day.isoformat(), 'end_date': day.isoformat()}
            self.assertEqual(self.get(resource, **params)['count'], 2)
            self.assertEqual(self.get(resource, 'summary/', **params)['total_revenue'], '20.20')

    def test_order_dates_use_receipt_business_date_with_unlinked_order_fallback(self):
        order = self.sale(order=True, encoded_at=timezone.now())
        yesterday = timezone.localdate() - timedelta(days=1)
        stamp = timezone.make_aware(datetime.combine(yesterday, time(12)))
        Transaction.objects.filter(pk=order.transaction_id).update(created_at=stamp)
        unlinked = Order.objects.create(customer=self.customer, handled_by=self.admin)
        Order.objects.filter(pk=unlinked.pk).update(created_at=stamp)
        params = {'start_date': yesterday.isoformat(), 'end_date': yesterday.isoformat()}
        self.assertEqual(self.get('orders', **params)['count'], 2)
        self.assertEqual(self.get('orders', 'summary/', **params)['total_revenue'], '10.10')
        today_order = self.sale(order=True)
        rows = self.get('orders', ordering='transaction_date')['results']
        self.assertEqual({row['id'] for row in rows[:2]}, {order.pk, unlinked.pk})
        self.assertEqual(rows[-1]['id'], today_order.pk)

    def test_pending_order_count_means_fulfilled_nonvoid_receipt_awaiting_payment(self):
        self.sale('10.10', order=True, payment_status='pending')
        self.sale('20.20', order=True, payment_status='paid')
        self.sale('30.30', order=True, payment_status='unknown')
        self.sale('40.40', order=True, payment_status='pending', order_status='cancelled', is_voided=True)
        self.sale('50.50', order=True, payment_status='pending', is_voided=True)
        self.sale('60.60', order=True, payment_status='pending', order_status='cancelled')
        Order.objects.create(customer=self.customer, handled_by=self.admin)
        self.assertEqual(self.get('orders', 'summary/'), {
            'order_count': 7, 'fulfilled_count': 5, 'cancelled_count': 2,
            'pending_payment_count': 1, 'total_revenue': '60.60'})
        self.assertEqual(self.get('orders', status='cancelled')['count'], 2)
        self.assertEqual(self.get('orders', status='fulfilled', payment_status='pending')['count'], 2)

    def test_staff_summaries_exclude_whole_hidden_inactive_and_mixed_sales(self):
        visible = self.batch('Visible')
        hidden = self.batch('Hidden', is_visible_to_staff=False)
        inactive = self.batch('Inactive', is_active=False)
        for batches in ((visible, visible), (hidden,), (visible, hidden), (inactive,)):
            order = self.sale(order=True, payment_status='pending')
            for batch in batches:
                self.attach(order.transaction, batch)
                OrderItem.objects.create(order=order, product=batch.product, quantity=1, unit_price=5, subtotal=5)
        self.assertEqual(self.get('orders', 'summary/')['pending_payment_count'], 4)
        self.client.force_authenticate(self.staff)
        for resource in ('transactions', 'orders'):
            self.assertEqual(self.get(resource)['count'], 1)
            self.assertEqual(self.get(resource, 'summary/')['total_revenue'], '10.10')
        self.assertEqual(self.get('orders', 'summary/')['pending_payment_count'], 1)

    def test_order_visibility_checks_order_and_receipt_items_independently(self):
        hidden = self.batch('Hidden Order', is_visible_to_staff=False)
        receipt_hidden = self.sale(order=True, payment_status='pending')
        self.attach(receipt_hidden.transaction, hidden)
        order_hidden = self.sale(order=True, payment_status='pending')
        OrderItem.objects.create(order=order_hidden, product=hidden.product, quantity=1, unit_price=5, subtotal=5)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.get('orders')['count'], 0)
        self.assertEqual(self.get('orders', 'summary/')['order_count'], 0)

    def test_staff_handled_by_filter_is_opt_in_and_does_not_change_shared_history(self):
        self.sale(order=True, handled_by=self.staff, payment_status='pending')
        self.sale(order=True, payment_status='pending')
        self.client.force_authenticate(self.staff)
        for resource, count in (('transactions', 'transaction_count'), ('orders', 'order_count')):
            self.assertEqual(self.get(resource, 'summary/')[count], 2)
            summary = self.get(resource, 'summary/', handled_by=self.staff.pk)
            self.assertEqual(summary[count], 1)
            self.assertEqual(summary['total_revenue'], '10.10')

    def test_encoding_inherits_filters_and_summary_without_including_other_sales(self):
        self.sale('4.20', encoded_at=timezone.now(), payment_status='pending')
        self.sale('100.00')
        self.assertEqual(self.get('encoding', search='buyer')['count'], 1)
        self.assertEqual(self.get('encoding', 'summary/')['total_revenue'], '4.20')

    def test_manually_encoded_cashier_is_the_encoder_in_all_history_consumers(self):
        for encoder in (self.admin, self.staff):
            order = self.sale(order=True, handled_by=encoder, encoded_at=timezone.now())
            expected = {'id': encoder.pk, 'username': encoder.username, 'role': encoder.role}
            for viewer in (self.admin, self.staff):
                self.client.force_authenticate(viewer)
                for url in (f'/sales/transactions/{order.transaction_id}/',
                            f'/sales/encoding/{order.transaction_id}/'):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200, response.data)
                    self.assertEqual(response.data['recorded_cashier'], expected)
                    self.assertTrue(response.data['is_manually_encoded'])
                for resource in ('transactions', 'encoding'):
                    rows = self.get(resource)['results']
                    row = next(row for row in rows if row['id'] == order.transaction_id)
                    self.assertEqual(row['recorded_cashier'], expected)
                for suffix in ('', f'{order.pk}/'):
                    response = self.client.get('/sales/orders/' + suffix)
                    self.assertEqual(response.status_code, 200, response.data)
                    row = response.data if suffix else next(row for row in response.data['results'] if row['id'] == order.pk)
                    self.assertEqual(row['transaction']['recorded_cashier'], expected)
                response = self.client.get(f'/sales/customers/{self.customer.pk}/transactions/')
                row = next(row for row in response.data['results'] if row['id'] == order.transaction_id)
                self.assertEqual(row['recorded_cashier'], expected)

    def test_imported_sale_keeps_unknown_cashier_even_if_it_has_an_encoding_timestamp(self):
        order = self.sale(order=True, source_reference='IMPORTED-CASHIER', encoded_at=timezone.now())
        for resource in ('transactions', 'encoding'):
            self.assertIsNone(self.get(resource)['results'][0]['recorded_cashier'])
        self.assertIsNone(self.get('orders')['results'][0]['transaction']['recorded_cashier'])

    def test_stable_ordering_across_pages_and_supported_sort_fields(self):
        orders = [self.sale(order=True, source_invoice_number=f'SI-{i}') for i in range(3)]
        for resource, ids in (('transactions', [order.transaction_id for order in orders]),
                              ('orders', [order.pk for order in orders])):
            for ordering in ('total_amount', '-total_amount', 'id', '-id', 'customer', 'handled_by', 'transaction_number', 'created_at'):
                with self.subTest(resource=resource, ordering=ordering):
                    pages = [self.get(resource, ordering=ordering, page_size=1, page=i + 1)['results'][0]['id'] for i in range(3)]
                    self.assertEqual(set(pages), set(ids))
                    if ordering in ('total_amount', '-total_amount', 'id', '-id'):
                        self.assertEqual(pages, sorted(ids, reverse=ordering.startswith('-')))

    def test_invalid_filters_return_400_on_lists_and_summaries(self):
        bad_filters = ({'start_date': 'invalid'}, {'end_date': '2026-02-30'},
            {'start_date': '2026-12-31', 'end_date': '2026-01-01'},
            {'handled_by': '1.5'}, {'customer_id': '9223372036854775808'},
            {'payment_status': 'invalid'}, {'payment_method': 'invalid'}, {'search': 'x' * 201},
            {'search': 'Milk\x00'})
        for resource in ('transactions', 'orders', 'encoding'):
            for suffix in ('', 'summary/'):
                for params in bad_filters:
                    with self.subTest(resource=resource, suffix=suffix, params=params):
                        response = self.client.get(f'/sales/{resource}/{suffix}', params)
                        self.assertEqual(response.status_code, 400, response.data)
            for ordering in ('items__unit_price', 'random()', 'id,-created_at'):
                self.assertEqual(self.client.get(f'/sales/{resource}/', {'ordering': ordering}).status_code, 400)
        for suffix in ('', 'summary/'):
            self.assertEqual(self.client.get('/sales/orders/' + suffix, {'status': 'pending'}).status_code, 400)

    def test_summaries_require_authentication_and_do_not_accept_writes(self):
        for resource in ('transactions', 'orders', 'encoding'):
            url = f'/sales/{resource}/summary/'
            self.assertEqual(self.client.post(url, {}, format='json').status_code, 405)
            self.client.force_authenticate(None)
            self.assertIn(self.client.get(url).status_code, (401, 403))
            self.client.force_authenticate(self.admin)

    def test_cache_refreshes_after_writes_payment_changes_and_cancellation(self):
        order = self.sale(order=True, payment_status='pending')
        self.get('transactions', 'summary/')
        self.get('orders', 'summary/')
        for resource in ('transactions', 'orders'):
            self.assertEqual(self.client.get(f'/sales/{resource}/summary/')['X-Data-Cache'], 'HIT')
        with self.capture_commit_callbacks(execute=True):
            order.transaction.payment_status = 'paid'
            order.transaction.save(update_fields=['payment_status'])
        self.assertEqual(self.get('orders', 'summary/')['pending_payment_count'], 0)
        self.assertEqual(self.get('transactions', 'summary/')['paid_amount'], '10.10')
        with self.capture_commit_callbacks(execute=True):
            order.transaction.is_voided = True
            order.transaction.save(update_fields=['is_voided'])
        self.assertEqual(self.get('transactions', 'summary/')['total_revenue'], '0.00')
        self.assertEqual(self.get('orders', 'summary/')['total_revenue'], '0.00')
        with self.capture_commit_callbacks(execute=True):
            self.sale('5.55', order=True, payment_status='pending')
        self.assertEqual(self.get('transactions', 'summary/')['total_revenue'], '5.55')
        self.assertEqual(self.get('orders', 'summary/')['pending_payment_count'], 1)

    def test_cached_staff_totals_follow_category_visibility_changes(self):
        batch = self.batch('Visibility Change')
        order = self.sale(order=True, payment_status='pending')
        self.attach(order.transaction, batch)
        self.client.force_authenticate(self.staff)
        for resource in ('transactions', 'orders'):
            self.assertEqual(self.get(resource, 'summary/')['total_revenue'], '10.10')
        # Bulk updates bypass signals; the staff visibility digest still protects cached reads.
        Category.objects.filter(pk=batch.product.category_id).update(is_visible_to_staff=False)
        for resource in ('transactions', 'orders'):
            self.assertEqual(self.get(resource, 'summary/')['total_revenue'], '0.00')

    def test_large_history_summary_uses_one_aggregate_without_serializing_receipts(self):
        sales = Transaction.objects.bulk_create([
            Transaction(handled_by=self.admin, customer=self.customer, total_amount=Decimal('0.10'), payment_status='pending')
            for _ in range(4801)])
        Order.objects.bulk_create([Order(customer=self.customer, handled_by=self.admin, transaction=sale) for sale in sales])
        for resource, count in (('transactions', 'transaction_count'), ('orders', 'order_count')):
            with patch('sales.serializers.transaction_serializer.TransactionSerializer.to_representation', side_effect=AssertionError('No receipt downloads')):
                with CaptureQueriesContext(connection) as queries:
                    data = self.get(resource, 'summary/', page_size=1)
            self.assertEqual(data[count], 4801)
            self.assertEqual(data['total_revenue'], '480.10')
            self.assertEqual(sum('SUM(' in query['sql'].upper() for query in queries), 1)
            self.assertFalse(any('FROM "sales_transactionitem"' in query['sql'] for query in queries))
            page = self.get(resource, page_size=1)
            self.assertEqual(page['count'], 4801)
            self.assertEqual(len(page['results']), 1)
            self.assertIsNotNone(page['next'])
