"""Behavioral and concurrency regressions for the October backend audit fixes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connections, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from config.test_support import APITestCase, APITransactionTestCase
from inventory.models import Category, Product, ProductBatch, StockAdjustment
from inventory.services.batch_service import BatchService
from reporting.services import inventory_status
from sales.models import Customer, Order, Transaction, StockReturn, SaleSubmission
from systemsetting.models import SystemSettings
from systemsetting.runtime import get_runtime_settings


class BusinessFixture:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.admin = get_user_model().objects.create_user(username='fix-admin', email='fix-admin@example.test', role='admin')
        self.staff = get_user_model().objects.create_user(username='fix-staff', email='fix-staff@example.test', role='staff')
        self.client.force_authenticate(self.admin)
        self.today = timezone.localdate()
        self.category = Category.objects.create(name='Fix dairy')
        self.product = Product.objects.create(category=self.category, name='Fresh Milk', unit='L', shelf_life=7)
        self.batch = ProductBatch.objects.create(product=self.product, batch_number='FIX-1', initial_quantity=10,
            remaining_quantity=10, unit_price=50, expiration_date=self.today+timedelta(days=7))

    def cart(self, **extra):
        return {'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '50.00'}],
                'payment_method': 'online', **extra}


class BackendFixTests(BusinessFixture, APITestCase):
    def test_staff_batch_reads_succeed_but_all_writes_are_denied(self):
        self.client.force_authenticate(self.staff)
        for (route, batch, field, item) in [('product-batches', self.batch, 'product_id', self.product)]:
            with self.subTest(route=route):
                base = f'/inventory/{route}/'
                detail = f'{base}{batch.pk}/'
                self.assertEqual(self.client.get(base).status_code, 200)
                self.assertEqual(self.client.get(detail).status_code, 200)
                payload = {field: item.pk, 'quantity': '5', 'expiration_date': batch.expiration_date.isoformat()}
                self.assertEqual(self.client.post(base, payload, format='json').status_code, 403)
                self.assertEqual(self.client.put(detail, payload, format='json').status_code, 403)
                self.assertEqual(self.client.patch(detail, {'unit_price': '999', 'notes': 'Edited'}, format='json').status_code, 403)
                self.assertEqual(self.client.delete(detail).status_code, 403)
                batch.refresh_from_db()
                self.assertEqual(batch.remaining_quantity, 10)

    def test_admin_batch_edit_and_receipt_work_but_received_history_cannot_be_deleted(self):
        route = f'/inventory/product-batches/{self.batch.pk}/'
        self.assertEqual(self.client.patch(route, {'notes': 'Reviewed'}, format='json').status_code, 200)
        self.assertEqual(self.client.delete(route).status_code, 400)
        self.assertTrue(ProductBatch.objects.filter(pk=self.batch.pk).exists())
        created = self.client.post('/inventory/product-batches/', {'product_id': self.product.pk,
            'quantity': '5', 'expiration_date': self.batch.expiration_date.isoformat()}, format='json')
        self.assertEqual(created.status_code, 201, created.data)


    def test_expired_disposal_counts_and_repeat_requests_are_consistent(self):
        for batch in (self.batch,):
            type(batch).objects.filter(pk=batch.pk).update(status='expired', expiration_date=self.today-timedelta(days=1))
        self.assertEqual(inventory_status()['expired_batch_count'], 1)
        for (field, batch) in [('product_batch_id', self.batch)]:
            with self.subTest(field=field):
                response = self.client.post('/inventory/stock-adjustments/', {field: batch.pk,
                    'adjustment_type': 'correction', 'quantity': '-1', 'unit_cost': '50'}, format='json')
                self.assertEqual(response.status_code, 400)
                payload = {field: batch.pk, 'adjustment_type': 'expired', 'quantity': '10', 'unit_cost': '50'}
                self.assertEqual(self.client.post('/inventory/stock-adjustments/', payload, format='json').status_code, 201)
                self.assertEqual(self.client.post('/inventory/stock-adjustments/', payload, format='json').status_code, 400)
                batch.refresh_from_db()
                self.assertEqual((batch.remaining_quantity, batch.status), (0, 'disposed'))
        self.assertEqual(StockAdjustment.objects.count(), 1)
        self.assertEqual(inventory_status()['expired_batch_count'], 0)

    def test_future_receipts_are_rejected_on_create_and_update(self):
        for (route, batch, field, item) in [('product-batches', self.batch, 'product_id', self.product)]:
            with self.subTest(route=route):
                payload = {field: item.pk, 'quantity': '5', 'date_received': (self.today+timedelta(days=1)).isoformat(),
                           'expiration_date': batch.expiration_date.isoformat()}
                self.assertEqual(self.client.post(f'/inventory/{route}/', payload, format='json').status_code, 400)
                self.assertEqual(self.client.patch(f'/inventory/{route}/{batch.pk}/', payload, format='json').status_code, 400)

    def test_legacy_future_stock_is_not_counted_or_consumed(self):
        ProductBatch.objects.filter(pk=self.batch.pk).update(date_received=self.today+timedelta(days=1))
        product = self.client.get(f'/inventory/products/{self.product.pk}/').data
        self.assertEqual(Decimal(str(product['total_stock'])), 0)
        self.assertEqual(self.client.post('/sales/checkout/', self.cart(), format='json').status_code, 400)
        self.assertTrue(all(item['quantity'] == 0 for item in inventory_status()['items']))
        self.assertEqual(inventory_status()['ledger']['rows'][0]['ending'], 0)

    def test_checkout_and_order_retries_keep_one_sale_and_reject_conflicting_keys(self):
        customer = Customer.objects.create(name='Fix customer', created_by=self.admin)
        for route in ('checkout', 'orders'):
            with self.subTest(route=route):
                payload = self.cart(request_id=str(uuid4()))
                if route == 'orders':
                    payload['customer_id'] = customer.pk
                first = self.client.post(f'/sales/{route}/', payload, format='json')
                again = self.client.post(f'/sales/{route}/', payload, format='json')
                self.assertEqual((first.status_code, again.status_code), (201, 200), (first.data, again.data))
                self.assertEqual(first.data['id'], again.data['id'])
                payload['amount_tendered'] = '100'
                self.assertEqual(self.client.post(f'/sales/{route}/', payload, format='json').status_code, 409)
        self.batch.refresh_from_db()
        self.assertEqual((Transaction.objects.count(), SaleSubmission.objects.count(), self.batch.remaining_quantity), (2, 2, 8))

    def test_optional_header_keys_validation_and_unkeyed_compatibility(self):
        key = str(uuid4())
        first = self.client.post('/sales/checkout/', self.cart(), format='json', HTTP_IDEMPOTENCY_KEY=key)
        again = self.client.post('/sales/checkout/', self.cart(request_id=key), format='json')
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(first.data['id'], again.data['id'])
        self.assertEqual(self.client.post('/sales/checkout/', self.cart(request_id=str(uuid4())), format='json', HTTP_IDEMPOTENCY_KEY=key).status_code, 400)
        self.assertEqual(self.client.post('/sales/checkout/', self.cart(request_id='bad'), format='json').status_code, 400)
        self.assertEqual(self.client.post('/sales/checkout/', self.cart(), format='json').status_code, 201)

    def test_failed_sale_does_not_reserve_the_retry_key(self):
        payload = self.cart(request_id=str(uuid4()))
        ProductBatch.objects.filter(pk=self.batch.pk).update(remaining_quantity=0, status='depleted')
        self.assertEqual(self.client.post('/sales/checkout/', payload, format='json').status_code, 400)
        self.assertFalse(SaleSubmission.objects.exists())
        self.assertFalse(Transaction.objects.exists())
        ProductBatch.objects.filter(pk=self.batch.pk).update(remaining_quantity=10, status='available')
        self.assertEqual(self.client.post('/sales/checkout/', payload, format='json').status_code, 201)

    def test_walkin_cancellation_preserves_stock_events_actor_and_permissions(self):
        sale = self.client.post('/sales/checkout/', self.cart(), format='json').data
        route = f"/sales/transactions/{sale['id']}/cancel/"
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(route, {}, format='json').status_code, 403)
        self.client.force_authenticate(self.admin)
        result = self.client.post(route, {'reason': 'Incorrect entry'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['is_voided'])
        self.assertEqual(result.data['voided_by'], self.admin.pk)
        self.assertEqual(result.data['void_reason'], 'Incorrect entry')
        self.assertIsNotNone(result.data['voided_at'])
        self.assertEqual(self.client.post(route, {}, format='json').status_code, 400)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 10)
        self.assertEqual(StockReturn.objects.get().quantity, 1)

    def test_transaction_cancellation_updates_linked_order_and_rejects_imports(self):
        customer = Customer.objects.create(name='Cancel customer', created_by=self.admin)
        response = self.client.post('/sales/orders/', self.cart(customer_id=customer.pk), format='json')
        sale_id = response.data['transaction']['id']
        self.assertEqual(self.client.post(f'/sales/transactions/{sale_id}/cancel/', {}, format='json').status_code, 200)
        self.assertEqual(Order.objects.get().status, 'cancelled')
        imported = Transaction.objects.create(handled_by=self.admin, source_reference='2025:IMPORT', total_amount=50)
        self.assertEqual(self.client.post(f'/sales/transactions/{imported.pk}/cancel/', {}, format='json').status_code, 400)
        imported.refresh_from_db()
        self.assertFalse(imported.is_voided)

    def test_failed_cancellation_rolls_back_stock_and_void_metadata(self):
        sale = self.client.post('/sales/checkout/', self.cart(), format='json').data
        with patch('sales.services.order_service.StockReturn.objects.create', side_effect=ValueError('event failed')):
            result = self.client.post(f"/sales/transactions/{sale['id']}/cancel/", {}, format='json')
        self.assertEqual(result.status_code, 400)
        self.batch.refresh_from_db()
        persisted = Transaction.objects.get(pk=sale['id'])
        self.assertEqual(self.batch.remaining_quantity, 9)
        self.assertFalse(persisted.is_voided)
        self.assertIsNone(persisted.voided_at)
        self.assertFalse(StockReturn.objects.exists())

    def test_imported_cashier_stays_unknown_and_manual_cashier_identifies_encoder(self):
        sale = Transaction.objects.create(handled_by=self.admin, source_reference='2025:OWNER')
        response = self.client.get(f'/sales/transactions/{sale.pk}/')
        self.assertIsNone(response.data['recorded_cashier'])
        self.assertEqual(response.data['handled_by']['id'], self.admin.pk)
        encoded = Transaction.objects.create(handled_by=self.admin, encoded_at=timezone.now())
        response = self.client.get(f'/sales/transactions/{encoded.pk}/')
        self.assertEqual(response.data['recorded_cashier'], {
            'id': self.admin.pk, 'username': self.admin.username, 'role': self.admin.role})
        self.assertEqual(response.data['handled_by']['id'], self.admin.pk)

    def test_deactivation_revokes_old_access_and_refresh_after_reactivation(self):
        from accounts.views.login_views import CooldownTokenObtainPairSerializer
        for method in ('patch', 'delete'):
            with self.subTest(method=method):
                token = CooldownTokenObtainPairSerializer.get_token(self.staff)
                old_client = APIClient()
                old_client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
                route = f'/accounts/users/{self.staff.pk}/'
                if method == 'patch':
                    result = self.client.patch(route, {'is_active': False}, format='json')
                else:
                    result = self.client.delete(route, {}, format='json')
                self.assertEqual(result.status_code, 200)
                self.assertEqual(self.client.patch(route, {'is_active': True}, format='json').status_code, 200)
                self.assertEqual(old_client.get('/accounts/user/').status_code, 401)
                self.assertEqual(old_client.post('/accounts/refresh/', {'refresh': str(token)}, format='json').status_code, 401)
                self.staff.refresh_from_db()

    def test_business_timezone_is_consistent_for_legacy_settings_and_new_updates(self):
        config = SystemSettings.get_config()
        config.timezone = 'Asia/Tokyo'
        with self.capture_commit_callbacks(execute=True):
            config.save()
        self.assertEqual(get_runtime_settings()['timezone'], settings.TIME_ZONE)
        self.assertEqual(self.client.get('/settings/system/').data['timezone'], settings.TIME_ZONE)
        self.assertEqual(self.client.patch('/settings/system/', {'timezone': 'Asia/Tokyo'}, format='json').status_code, 400)
        self.assertEqual(self.client.patch('/settings/system/', {'timezone': settings.TIME_ZONE}, format='json').status_code, 200)

    def test_live_sales_update_actuals_without_hiding_or_recomputing_saved_predictions(self):
        from forecasting import beta_graph as graph
        runs = {}
        for period in graph.PERIODS:
            with patch('forecasting.beta_graph._train', return_value={'state': {'test': True}}), \
                 patch('forecasting.beta_graph._point', return_value=100.0):
                runs[period], _ = graph.refresh(period=period)
        sale = self.client.post('/sales/checkout/', self.cart(), format='json')
        self.assertEqual(sale.status_code, 201, sale.data)
        with patch('forecasting.beta_graph._train', side_effect=AssertionError('GET must not train')), \
             patch('forecasting.beta_graph._point', side_effect=AssertionError('Read original saved values')):
            for period, run in runs.items():
                with self.subTest(period=period):
                    response = graph.report(period=period)
                    self.assertEqual(response['status'], 'ready')
                    self.assertEqual(response['model_id'], run.pk)
                    current = next(p for p in response['views']['regular']['points'] if p['is_current'])
                    self.assertEqual(current['actual'], 50)
                    if period == 'yearly':
                        # This fixture has no completed-month history or valid
                        # monthly model path for the current annual outlook.
                        self.assertIsNone(current['future_prediction'])
                        self.assertEqual(current['annual_outlook']['current_month_actual'], 50)
                    else:
                        self.assertEqual(current['future_prediction'], 100)
                    self.assertTrue(current['is_partial_period'])

    def test_completed_history_correction_still_requires_refresh(self):
        from forecasting import beta_graph as graph
        old_day = self.today.replace(day=1)-timedelta(days=1)
        sale = Transaction.objects.create(handled_by=self.admin, source_sales_segment='regular', total_amount=100)
        Transaction.objects.filter(pk=sale.pk).update(created_at=timezone.make_aware(datetime.combine(old_day, datetime.min.time())))
        with patch('forecasting.beta_graph._train', return_value={'state': {'test': True}}), \
             patch('forecasting.beta_graph._point', return_value=100.0):
            graph.refresh()
        Transaction.objects.filter(pk=sale.pk).update(total_amount=999)
        with patch('forecasting.beta_graph._train', side_effect=AssertionError('GET must not train')):
            self.assertEqual(graph.report()['status'], 'refresh_required')


class BackendFixConcurrencyTests(BusinessFixture, APITransactionTestCase):
    def parallel_requests(self, requests):
        barrier = Barrier(len(requests))
        SystemSettings.get_config()

        def send(url, payload):
            try:
                client = APIClient()
                client.force_authenticate(self.admin)
                barrier.wait(timeout=5)
                result = client.post(url, payload, format='json')
                return result.status_code, result.data
            finally:
                connections['default'].close()

        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            futures = [pool.submit(send, *request) for request in requests]
            return [future.result(timeout=15) for future in futures]

    def test_ten_checkouts_cannot_oversell_the_last_three_units(self):
        ProductBatch.objects.filter(pk=self.batch.pk).update(remaining_quantity=3, status='available')
        responses = self.parallel_requests([('/sales/checkout/', self.cart()) for _ in range(10)])
        self.assertEqual(sorted(status for status, _ in responses), [201] * 3 + [400] * 7, responses)
        self.assertEqual(Transaction.objects.count(), 3)
        self.assertEqual(sum(Transaction.objects.values_list('total_amount', flat=True)), Decimal('150.00'))
        self.assertFalse(SaleSubmission.objects.exists())
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 0)
        self.assertEqual(self.batch.status, 'depleted')

    def test_concurrent_keyed_checkout_and_order_retries_use_last_stock_once(self):
        customer = Customer.objects.create(name='Concurrent buyer', created_by=self.admin)
        for route in ('checkout', 'orders'):
            with self.subTest(route=route):
                ProductBatch.objects.filter(pk=self.batch.pk).update(remaining_quantity=1, status='available')
                payload = self.cart(request_id=str(uuid4()))
                if route == 'orders':
                    payload['customer_id'] = customer.pk
                request = (f'/sales/{route}/', payload)
                responses = self.parallel_requests([request, request])
                self.assertEqual(sorted(status for status, _ in responses), [200, 201], responses)
                self.assertEqual(responses[0][1]['id'], responses[1][1]['id'])
                self.batch.refresh_from_db()
                self.assertEqual(self.batch.remaining_quantity, 0)
        self.assertEqual(Transaction.objects.count(), 2)
        self.assertEqual(SaleSubmission.objects.count(), 2)

    def test_order_and_transaction_cancellation_cannot_restore_twice(self):
        customer = Customer.objects.create(name='Concurrent cancellation', created_by=self.admin)
        sale = self.client.post('/sales/orders/', self.cart(customer_id=customer.pk), format='json')
        self.assertEqual(sale.status_code, 201, sale.data)
        responses = self.parallel_requests([
            (f"/sales/orders/{sale.data['id']}/cancel/", {}),
            (f"/sales/transactions/{sale.data['transaction']['id']}/cancel/", {}),
        ])
        self.assertEqual(sorted(status for status, _ in responses), [200, 400], responses)
        self.assertEqual(StockReturn.objects.count(), 1)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 10)

    def test_runtime_cache_is_invalidated_after_commit_even_with_another_reader(self):
        config = SystemSettings.get_config()
        config.business_name = 'Before commit'
        config.save()
        self.assertEqual(get_runtime_settings()['business_name'], 'Before commit')

        def read():
            try:
                return get_runtime_settings()['business_name']
            finally:
                connections['default'].close()

        with transaction.atomic():
            config.business_name = 'After commit'
            config.save()
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(read).result(timeout=5), 'Before commit')
        self.assertEqual(get_runtime_settings()['business_name'], 'After commit')

    def test_rolled_back_settings_do_not_clear_committed_runtime_cache(self):
        config = SystemSettings.get_config()
        self.assertEqual(get_runtime_settings()['currency'], 'PHP')
        with self.assertRaises(ValueError):
            with transaction.atomic():
                config.currency = 'USD'
                config.save()
                raise ValueError('rollback')
        self.assertEqual(get_runtime_settings()['currency'], 'PHP')

    def test_cold_cache_cannot_publish_uncommitted_settings(self):
        config = SystemSettings.get_config()
        cache.clear()
        with self.assertRaises(ValueError):
            with transaction.atomic():
                config.currency = 'USD'
                config.save()
                self.assertEqual(get_runtime_settings()['currency'], 'USD')
                raise ValueError('rollback')
        self.assertEqual(get_runtime_settings()['currency'], 'PHP')
