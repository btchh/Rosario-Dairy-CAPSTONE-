"""Cache outages must not hide committed inventory, sales or settings changes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Event
from unittest.mock import patch
import warnings

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.cache.backends.base import CacheKeyWarning
from django.db import OperationalError, connections, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.response import Response

from config.cache_invalidation import cache_namespace
from config.data_cache import NAMESPACE, VERSION_KEY, cache_api_response, data_version
from config.models import CacheInvalidation
from config.test_support import APIClient, APITestCase, APITransactionTestCase
from inventory.models import Category, Product, ProductBatch
from reporting.services import get_report
from systemsetting.models import SystemSettings
from systemsetting.runtime import (
    RUNTIME_NAMESPACE, get_runtime_settings,
)


CACHE_SETTINGS = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                               'LOCATION': 'durable-cache-regressions'}}


class CacheFixture:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.admin = get_user_model().objects.create_user(
            username='cache-consistency', role='admin', email='cache-consistency@example.test')
        self.client.force_authenticate(self.admin)
        category = Category.objects.create(name='Cache dairy')
        self.product = Product.objects.create(name='Cache milk', category=category, unit='L', shelf_life=7)
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='CACHE-CONSISTENCY', initial_quantity=10,
            remaining_quantity=10, unit_price=50,
            expiration_date=timezone.localdate()+timedelta(days=7))
        SystemSettings.get_config()

    def sale(self):
        return self.client.post('/sales/checkout/', {
            'items': [{'product_id': self.product.pk, 'quantity': '3', 'unit_price': '50'}],
            'payment_method': 'online'}, format='json')

    def stock(self, client=None):
        response = (client or self.client).get('/inventory/products/')
        self.assertEqual(response.status_code, 200, response.data)
        return Decimal(str(response.data[0]['total_stock']))


@override_settings(CACHES=CACHE_SETTINGS)
class CacheOutageTests(CacheFixture, APITestCase):
    def test_staff_reports_keep_cache_keys_within_backend_limits(self):
        staff = get_user_model().objects.create_user(username='cache-staff', role='staff')
        self.client.force_authenticate(staff)
        with warnings.catch_warnings():
            warnings.simplefilter('error', CacheKeyWarning)
            response = self.client.get('/api/reports/preview/', {
                'type': 'inventory', 'start_date': '2024-01-01', 'end_date': '2024-12-31'})
        self.assertEqual(response.status_code, 200, response.data)

    def test_failed_rotation_retires_api_report_and_rendered_history_payloads(self):
        self.assertEqual(self.stock(), 10)
        self.assertEqual(self.client.get('/sales/transactions/').json()['count'], 0)
        self.assertEqual(get_report('inventory')['items'][0]['quantity'], 10)
        old = data_version()
        with patch('config.cache_invalidation._rotate', side_effect=ConnectionError('cache unavailable')), \
                self.capture_commit_callbacks(execute=True):
            sale = self.sale()
        self.assertEqual(sale.status_code, 201, sale.data)
        self.assertNotEqual(data_version(), old)
        self.assertEqual(self.stock(), 7)
        self.assertEqual(self.client.get('/sales/transactions/').json()['count'], 1)
        self.assertEqual(get_report('inventory')['items'][0]['quantity'], 7)
        self.assertEqual(self.client.get('/sales/transactions/')['X-Data-Cache'], 'HIT')

    def test_rollback_restores_original_namespace_and_cached_values(self):
        self.assertEqual(self.stock(), 10)
        before = data_version()
        markers = set(CacheInvalidation.objects.values_list('pk', flat=True))
        with self.assertRaises(ValueError):
            with transaction.atomic():
                self.batch.remaining_quantity = 2
                self.batch.save()
                self.assertEqual(self.stock(), 2)
                raise ValueError('roll back the stock change')
        self.assertEqual(data_version(), before)
        self.assertEqual(set(CacheInvalidation.objects.values_list('pk', flat=True)), markers)
        self.assertEqual(self.stock(), 10)

    def test_unverifiable_database_guard_never_serves_warm_business_data(self):
        self.assertEqual(self.stock(), 10)
        with patch('config.cache_invalidation.CacheInvalidation.objects.using',
                   side_effect=OperationalError('private connection details')):
            response = self.client.get('/inventory/products/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('Cache milk', str(response.data))
        self.assertNotIn('private connection details', str(response.data))

    def test_failed_runtime_invalidation_cannot_restore_old_preferences(self):
        self.assertEqual(get_runtime_settings()['currency'], 'PHP')
        config = SystemSettings.get_config()
        with patch('config.cache_invalidation._rotate', side_effect=ConnectionError), \
                self.capture_commit_callbacks(execute=True):
            config.currency = 'USD'
            config.save()
        self.assertEqual(get_runtime_settings()['currency'], 'USD')
        self.assertTrue(CacheInvalidation.objects.filter(namespace=RUNTIME_NAMESPACE).exists())


@override_settings(CACHES=CACHE_SETTINGS)
class CacheCommitTests(CacheFixture, APITransactionTestCase):
    def other_reader_stock(self):
        try:
            client = APIClient()
            client.force_authenticate(self.admin)
            return self.stock(client)
        finally:
            connections['default'].close()

    def test_other_reader_sees_committed_change_during_sustained_cache_failure(self):
        self.assertEqual(self.stock(), 10)
        with patch('config.cache_invalidation._rotate', side_effect=ConnectionError):
            with transaction.atomic():
                self.batch.remaining_quantity = 3
                self.batch.save()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    self.assertEqual(pool.submit(self.other_reader_stock).result(timeout=10), 10)
            self.assertTrue(CacheInvalidation.objects.filter(namespace=NAMESPACE).exists())
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(self.other_reader_stock).result(timeout=10), 3)
        self.assertEqual(self.stock(), 3)
        self.assertFalse(CacheInvalidation.objects.exists())
        self.stock()
        self.assertEqual(self.client.get('/inventory/products/')['X-Data-Cache'], 'HIT')

    def test_separate_process_rejects_stale_file_cache_after_failed_invalidation(self):
        import json
        import subprocess
        import sys
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory, override_settings(CACHES={'default': {
            'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache', 'LOCATION': directory,
        }}):
            self.assertEqual(self.stock(), 10)
            with patch('config.cache_invalidation._rotate', side_effect=ConnectionError):
                self.batch.remaining_quantity = 3
                self.batch.save()
            script = '''
import json, os, sys
os.environ['DB_NAME'] = sys.argv[2]
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
from django.conf import settings
settings.CACHES = {'default': {
    'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache', 'LOCATION': sys.argv[1]}}
settings.ALLOWED_HOSTS = ['testserver']
import django
django.setup()
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from unittest.mock import patch
client = APIClient()
client.force_authenticate(get_user_model().objects.get(username='cache-consistency'))
with patch('config.cache_invalidation._rotate', side_effect=ConnectionError):
    response = client.get('/inventory/products/')
assert response.status_code == 200
print(json.dumps({'stock': str(response.data[0]['total_stock'])}))
'''
            result = subprocess.run([sys.executable, '-c', script, directory,
                connections['default'].settings_dict['NAME']], capture_output=True, text=True, check=True)
            self.assertEqual(Decimal(json.loads(result.stdout)['stock']), 3)

    def test_late_old_reader_cannot_poison_new_namespace_after_failed_invalidation(self):
        read_finished, release = Event(), Event()

        class SlowReader:
            @cache_api_response
            def get(inner, request):
                value = ProductBatch.objects.get(pk=self.batch.pk).remaining_quantity
                read_finished.set()
                if not release.wait(timeout=10):
                    raise AssertionError('Reader was not released')
                return Response({'remaining': str(value)})

        def read():
            try:
                from rest_framework.test import APIRequestFactory, force_authenticate
                from rest_framework.request import Request
                raw = APIRequestFactory().get('/slow-cached-stock/')
                force_authenticate(raw, self.admin)
                return SlowReader().get(Request(raw)).data
            finally:
                connections['default'].close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            old_reader = pool.submit(read)
            try:
                self.assertTrue(read_finished.wait(timeout=10))
                with patch('config.cache_invalidation._rotate', side_effect=ConnectionError):
                    self.batch.remaining_quantity = 4
                    self.batch.save()
                    release.set()
                    self.assertEqual(old_reader.result(timeout=10)['remaining'], '10.00')
                    self.assertEqual(self.stock(), 4)
            finally:
                release.set()
        self.assertEqual(self.stock(), 4)

    def test_recovery_cannot_remove_a_later_concurrent_writers_marker(self):
        with patch('config.cache_invalidation._rotate', side_effect=ConnectionError):
            self.batch.remaining_quantity = 8
            self.batch.save()
        from config.cache_invalidation import _rotate
        rotated, release = Event(), Event()

        def rotate_then_wait(key):
            value = _rotate(key)
            rotated.set()
            if not release.wait(timeout=10):
                raise AssertionError('Recovery reader was not released')
            return value

        def recover():
            try:
                with patch('config.cache_invalidation._rotate', side_effect=rotate_then_wait):
                    return cache_namespace(NAMESPACE, VERSION_KEY)
            finally:
                connections['default'].close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            reader = pool.submit(recover)
            try:
                self.assertTrue(rotated.wait(timeout=10))
                with patch('config.cache_invalidation._rotate', side_effect=ConnectionError):
                    self.batch.remaining_quantity = 6
                    self.batch.save()
                release.set()
                reader.result(timeout=10)
            finally:
                release.set()
        self.assertTrue(CacheInvalidation.objects.filter(namespace=NAMESPACE).exists())
        self.assertEqual(self.stock(), 6)
        self.assertFalse(CacheInvalidation.objects.exists())

    def test_cache_backend_false_result_retains_pending_change(self):
        self.assertEqual(self.stock(), 10)
        original_set = cache.set

        def refuse_version(key, *args, **kwargs):
            return False if key == VERSION_KEY else original_set(key, *args, **kwargs)

        with patch('config.cache_invalidation.cache.set', side_effect=refuse_version):
            self.batch.remaining_quantity = 5
            self.batch.save()
            self.assertTrue(CacheInvalidation.objects.filter(namespace=NAMESPACE).exists())
            self.assertEqual(self.stock(), 5)
        self.assertEqual(self.stock(), 5)

    def test_large_outage_queue_remains_fresh_and_recovers_in_bounded_batches(self):
        self.assertEqual(self.stock(), 10)
        CacheInvalidation.objects.bulk_create([
            CacheInvalidation(namespace=NAMESPACE) for _ in range(1002)])
        with patch('config.cache_invalidation._rotate', side_effect=ConnectionError):
            ProductBatch.objects.filter(pk=self.batch.pk).update(remaining_quantity=5)
            self.assertEqual(self.stock(), 5)
            ProductBatch.objects.filter(pk=self.batch.pk).update(remaining_quantity=2)
            self.assertEqual(self.stock(), 2)
        self.assertEqual(self.stock(), 2)
        self.assertEqual(CacheInvalidation.objects.filter(namespace=NAMESPACE).count(), 2)
        self.assertEqual(self.stock(), 2)
        self.assertFalse(CacheInvalidation.objects.exists())

    def test_cleanup_database_failure_preserves_successful_sale_and_fresh_reads(self):
        self.assertEqual(self.stock(), 10)
        with patch('django.db.models.query.QuerySet.delete', side_effect=OperationalError('cleanup unavailable')):
            response = self.sale()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(CacheInvalidation.objects.exists())
        self.assertEqual(self.stock(), 7)

    def test_runtime_settings_recover_after_failed_rotation_and_concurrent_reader(self):
        self.assertEqual(get_runtime_settings()['currency'], 'PHP')
        config = SystemSettings.get_config()

        def read_currency():
            try:
                return get_runtime_settings()['currency']
            finally:
                connections['default'].close()

        with patch('config.cache_invalidation._rotate', side_effect=ConnectionError):
            with transaction.atomic():
                config.currency = 'USD'
                config.save()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    self.assertEqual(pool.submit(read_currency).result(timeout=10), 'PHP')
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(read_currency).result(timeout=10), 'USD')
            self.assertTrue(CacheInvalidation.objects.filter(namespace=RUNTIME_NAMESPACE).exists())
        self.assertEqual(get_runtime_settings()['currency'], 'USD')
        self.assertFalse(CacheInvalidation.objects.filter(namespace=RUNTIME_NAMESPACE).exists())
