"""A later report refresh must never undo a committed checkout."""
from datetime import timedelta
from decimal import Decimal
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError

from config.test_support import APITransactionTestCase
from inventory.models import Category, Product, ProductBatch
from sales.models import Transaction


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ReportRefreshIsolationTests(APITransactionTestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(username='refresh-isolation',
            email='refresh-isolation@example.test', role='admin')
        self.staff = get_user_model().objects.create_user(username='refresh-isolation-staff',
            email='refresh-isolation-staff@example.test', role='staff')
        self.product = Product.objects.create(name='Milk', unit='L', shelf_life=7,
            category=Category.objects.create(name='Refresh dairy'))
        self.batch = ProductBatch.objects.create(product=self.product, batch_number='REFRESH-ISOLATION',
            initial_quantity=10, remaining_quantity=10, unit_price=100,
            expiration_date=timezone.localdate()+timedelta(days=7))
        self.client.force_authenticate(self.admin)

    def checkout(self):
        response = self.client.post('/sales/checkout/', {
            'items': [{'product_id': self.product.pk, 'quantity': '1.00', 'unit_price': '100.00'}],
            'payment_method': 'cash', 'amount_tendered': '100.00',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['id']

    def assert_committed(self, ids):
        # TransactionTestCase uses real commits; reconnect to verify durable rows.
        connection.close()
        self.assertEqual(set(Transaction.objects.values_list('id', flat=True)), set(ids))
        self.assertEqual(list(Transaction.objects.values_list('total_amount', flat=True)),
                         [Decimal('100.00')] * len(ids))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal(10-len(ids)))

    def test_two_successful_checkouts_stay_committed_when_second_refresh_is_throttled(self):
        with patch('reporting.views.refresh_reports', return_value=(timezone.now(), {})) as rebuild:
            first = self.checkout()
            self.assertEqual(self.client.post('/api/reports/refresh/', {}).status_code, 200)
            second = self.checkout()
            response = self.client.post('/api/reports/refresh/', {})
        self.assertEqual(response.status_code, 429, response.data)
        self.assertGreater(int(response['Retry-After']), 0)
        self.assertEqual(rebuild.call_count, 2)  # First refresh builds admin and staff scopes.
        self.assert_committed([first, second])

    def test_global_cooldown_also_limits_another_authorized_account(self):
        with patch('reporting.views.refresh_reports', return_value=(timezone.now(), {})) as rebuild:
            self.assertEqual(self.client.post('/api/reports/refresh/', {}).status_code, 200)
            self.client.force_authenticate(self.staff)
            response = self.client.post('/api/reports/refresh/', {})
        self.assertEqual(response.status_code, 429, response.data)
        self.assertEqual(rebuild.call_count, 2)

    def test_account_throttle_cache_read_and_write_failures_return_safe_503(self):
        for operation in ('get', 'set'):
            cache.clear()
            with self.subTest(operation=operation), \
                    patch('rest_framework.throttling.SimpleRateThrottle.cache.'+operation,
                          side_effect=RedisConnectionError('private cache credentials')), \
                    patch('reporting.views.refresh_reports') as rebuild:
                response = self.client.post('/api/reports/refresh/', {})
            self.assertEqual(response.status_code, 503, response.data)
            self.assertEqual(response.data['detail'].code, 'rate_limit_unavailable')
            self.assertNotIn('private', str(response.data))
            rebuild.assert_not_called()

    def test_global_cooldown_cache_failure_returns_503_preserves_sale_and_releases_lock(self):
        sale = self.checkout()
        with patch('reporting.services.cache.add', side_effect=RedisConnectionError('private cache host')), \
                patch('reporting.views.refresh_reports') as rebuild:
            response = self.client.post('/api/reports/refresh/', {})
        self.assertEqual(response.status_code, 503, response.data)
        self.assertNotIn('private', str(response.data))
        rebuild.assert_not_called()
        self.assert_committed([sale])
        cache.clear()
        with patch('reporting.views.refresh_reports', return_value=(timezone.now(), {})):
            self.assertEqual(self.client.post('/api/reports/refresh/', {}).status_code, 200)

    def test_refresh_becomes_available_after_the_one_minute_cooldown(self):
        now = time.time()
        with patch('reporting.views.refresh_reports', return_value=(timezone.now(), {})):
            self.assertEqual(self.client.post('/api/reports/refresh/', {}).status_code, 200)
            self.assertEqual(self.client.post('/api/reports/refresh/', {}).status_code, 429)
            with patch('accounts.throttles.ReportRefreshRateThrottle.timer', return_value=now+61), \
                    patch('django.core.cache.backends.locmem.time.time', return_value=now+61):
                self.assertEqual(self.client.post('/api/reports/refresh/', {}).status_code, 200)
