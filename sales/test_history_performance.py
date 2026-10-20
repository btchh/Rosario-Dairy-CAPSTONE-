import gzip
import json
import subprocess
import sys
import time as clock_time
from tempfile import TemporaryDirectory
from datetime import datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.renderers import JSONRenderer

from inventory.models import Category, FEFOConf, Product, ProductBatch
from inventory.serializers import ProdBatchSerializer
from sales.models import Transaction, TransactionItem, Order, OrderItem, Customer
from sales.serializers import TransactionSerializer
from sales.views.transaction_views import TransactionViewSet


class SalesHistoryPerformanceTests(TestCase):
    def setUp(self):
        cache.clear()
        FEFOConf.get_config()
        self.user = get_user_model().objects.create_user(
            username='history-performance', email='history-performance@example.com', role='admin',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        category = Category.objects.create(name='Milk')
        product = Product.objects.create(
            category=category, name='Milk', unit='L',  shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=product, batch_number='HISTORY-PERF', initial_quantity=100,
            remaining_quantity=100, expiration_date=timezone.localdate() + timedelta(days=7),
        )
        self.sale = Transaction.objects.create(handled_by=self.user, subtotal=50, total_amount=50)
        for quantity in (1, 2, 3):
            TransactionItem.objects.create(
                transaction=self.sale, product_batch=self.batch,
                quantity=quantity, unit_price=50, product_name_snapshot=f'Milk snapshot {quantity}',
            )

    def test_repeated_batch_details_are_serialized_once_without_changing_shape(self):
        original = ProdBatchSerializer.to_representation
        expected = ProdBatchSerializer(self.batch).data
        with patch.object(ProdBatchSerializer, 'to_representation', autospec=True, side_effect=original) as serialize:
            data = TransactionSerializer(self.sale).data
        self.assertEqual(serialize.call_count, 1)
        for index, item in enumerate(data['items'], start=1):
            self.assertEqual(item['product_batch'], expected)
            self.assertEqual(item['quantity'], f'{index}.00')
            self.assertEqual(item['product_name_snapshot'], f'Milk snapshot {index}')

    def test_separate_batches_and_requests_keep_current_values(self):
        another = ProductBatch.objects.create(
            product=self.batch.product, batch_number='HISTORY-OTHER', initial_quantity=30,
            remaining_quantity=30, expiration_date=self.batch.expiration_date,
        )
        TransactionItem.objects.create(transaction=self.sale, product_batch=another, quantity=1, unit_price=40)
        data = TransactionSerializer(self.sale).data
        quantities = {item['product_batch']['id']: item['product_batch']['remaining_quantity'] for item in data['items']}
        self.assertEqual(quantities, {self.batch.pk: '100.00', another.pk: '30.00'})
        self.batch.remaining_quantity = 10
        self.batch.save()
        fresh = TransactionSerializer(Transaction.objects.get(pk=self.sale.pk)).data
        self.assertEqual(fresh['items'][0]['product_batch']['remaining_quantity'], '10.00')

    def test_gzip_preserves_cached_history_data_and_plain_clients_still_work(self):
        endpoint = '/sales/transactions/'
        plain = self.client.get(endpoint)
        compressed = self.client.get(endpoint, HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(compressed.status_code, 200)
        self.assertEqual(compressed['Content-Encoding'], 'gzip')
        self.assertIn('Accept-Encoding', compressed['Vary'])
        self.assertLess(len(compressed.content), len(plain.content))
        self.assertEqual(json.loads(gzip.decompress(compressed.content)), json.loads(plain.content))
        repeated = self.client.get(endpoint)
        self.assertNotIn('Content-Encoding', repeated)
        self.assertEqual(repeated.content, plain.content)

    def test_fetch_shares_batch_objects_and_has_bounded_queries(self):
        view = TransactionViewSet()
        view.action = 'list'
        view.request = type('Request', (), {'user': self.user})()
        # One shared global-threshold read, independent of the number of items.
        with self.assertNumQueries(4):
            sale = list(view.get_queryset())[0]
            items = list(sale.items.all())
            data = TransactionSerializer(sale).data
        self.assertTrue(all(item.product_batch is items[0].product_batch for item in items))
        self.assertEqual(data['items'][0]['product_batch']['product']['total_stock'], 100)

    def test_warm_history_reuses_rendered_json_and_indented_clients_are_separate(self):
        original = JSONRenderer.render
        with patch.object(JSONRenderer, 'render', autospec=True, side_effect=original) as render:
            plain = self.client.get('/sales/transactions/')
            repeated = self.client.get('/sales/transactions/')
            self.assertEqual(render.call_count, 1)
            pretty = self.client.get('/sales/transactions/', HTTP_ACCEPT='application/json; indent=4')
            self.assertEqual(render.call_count, 2)
        self.assertEqual(repeated.content, plain.content)
        self.assertEqual(json.loads(pretty.content), json.loads(plain.content))
        self.assertIn(b'\n', pretty.content)

    def test_commit_invalidates_rendered_history(self):
        first = self.client.get('/sales/transactions/')
        self.assertEqual(first.json()['count'], 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.sale.is_voided = True
            self.sale.save()
        self.assertEqual(self.client.get('/sales/transactions/').json()['count'], 0)

    def test_timestamp_ties_are_ordered_consistently_across_pages(self):
        another = Transaction.objects.create(handled_by=self.user, total_amount=50)
        Transaction.objects.filter(pk=another.pk).update(created_at=self.sale.created_at)
        first = self.client.get('/sales/transactions/?page_size=1&page=1').json()
        second = self.client.get('/sales/transactions/?page_size=1&page=2').json()
        self.assertEqual(first['results'][0]['id'], another.pk)
        self.assertEqual(second['results'][0]['id'], self.sale.pk)

    def test_date_filter_includes_the_entire_local_day(self):
        day = timezone.localdate()
        begin = timezone.make_aware(datetime.combine(day, time.min))
        end = timezone.make_aware(datetime.combine(day, time.max))
        Transaction.objects.filter(pk=self.sale.pk).update(created_at=begin)
        included = Transaction.objects.create(handled_by=self.user, total_amount=50)
        before = Transaction.objects.create(handled_by=self.user, total_amount=50)
        after = Transaction.objects.create(handled_by=self.user, total_amount=50)
        Transaction.objects.filter(pk=included.pk).update(created_at=end)
        Transaction.objects.filter(pk=before.pk).update(created_at=begin - timedelta(microseconds=1))
        Transaction.objects.filter(pk=after.pk).update(created_at=end + timedelta(microseconds=1))
        data = self.client.get(f'/sales/transactions/?start_date={day}&end_date={day}').json()
        self.assertEqual(data['count'], 2)
        self.assertEqual({row['id'] for row in data['results']}, {self.sale.pk, included.pk})

    def test_history_stays_warm_beyond_five_minutes(self):
        started = clock_time.time()
        first = self.client.get('/sales/transactions/')
        self.assertEqual(first['X-Data-Cache'], 'MISS')
        with patch('time.time', return_value=started + 301):
            self.assertEqual(self.client.get('/sales/transactions/')['X-Data-Cache'], 'HIT')
        with patch('time.time', return_value=started + 3601):
            self.assertEqual(self.client.get('/sales/transactions/')['X-Data-Cache'], 'MISS')

    def test_loading_a_shared_payload_does_not_extend_its_expiry(self):
        from config.cache_payloads import _memory, get_payload, set_payload
        started = clock_time.time()
        key = 'shared-payload-expiry-test'
        with patch('time.time', return_value=started):
            set_payload(key, {'count': 1}, 10)
        _memory.delete(key)
        with patch('time.time', return_value=started + 9):
            self.assertEqual(get_payload(key), {'count': 1})
        with patch('time.time', return_value=started + 11):
            self.assertIsNone(get_payload(key))

    def test_authentication_metadata_does_not_evict_history(self):
        self.client.get('/sales/transactions/')
        with self.captureOnCommitCallbacks(execute=True):
            self.user.last_login = timezone.now()
            self.user.save(update_fields=['last_login'])
            self.user.failed_login_attempts = 0
            self.user.save(update_fields=['failed_login_attempts', 'locked_until'])
        self.assertEqual(self.client.get('/sales/transactions/')['X-Data-Cache'], 'HIT')
        with self.captureOnCommitCallbacks(execute=True):
            self.user.username = 'renamed-history-user'
            self.user.save(update_fields=['username'])
        renamed = self.client.get('/sales/transactions/')
        self.assertEqual(renamed['X-Data-Cache'], 'MISS')
        self.assertEqual(renamed.json()['results'][0]['handled_by']['username'], 'renamed-history-user')

    def test_file_cache_shares_payloads_and_invalidations_across_processes(self):
        with TemporaryDirectory() as directory, override_settings(CACHES={
            'default': {'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache', 'LOCATION': directory},
        }):
            first = self.client.get('/sales/transactions/')
            script = '''
import json, sys, uuid
from django.conf import settings
settings.configure()
from django.core.cache.backends.filebased import FileBasedCache
cache = FileBasedCache(sys.argv[1], {})
key = sys.argv[2]
assert cache.get(key)[1]['count'] == 1
assert json.loads(cache.get(key + ':json:None')[1])['count'] == 1
cache.set('business-data:version:v1', uuid.uuid4().hex, timeout=None)
'''
            subprocess.run([sys.executable, '-c', script, directory, first.data_cache_key], check=True)
            self.assertEqual(self.client.get('/sales/transactions/')['X-Data-Cache'], 'MISS')

    def test_orders_reuse_rendered_json_and_shared_product_objects(self):
        customer = Customer.objects.create(name='Order Customer', created_by=self.user)
        order = Order.objects.create(customer=customer, handled_by=self.user, transaction=self.sale)
        for quantity in (1, 2, 3):
            OrderItem.objects.create(order=order, product=self.batch.product, quantity=quantity,
                                     unit_price=50, subtotal=quantity * 50)
        from sales.views.order_views import OrderViewSet
        from sales.serializers.order_serializer import OrderListSerializer
        view = OrderViewSet()
        view.action = 'list'
        view.request = type('Request', (), {'user': self.user, 'query_params': {}})()
        with self.assertNumQueries(3):
            loaded = list(view.get_queryset())[0]
            items = list(loaded.items.all())
            data = OrderListSerializer(loaded).data
        self.assertTrue(all(item.product is items[0].product for item in items))
        self.assertEqual(data['items'][0]['product']['name'], 'Milk')
        original = JSONRenderer.render
        with patch.object(JSONRenderer, 'render', autospec=True, side_effect=original) as render:
            first = self.client.get('/sales/orders/')
            repeated = self.client.get('/sales/orders/')
            self.assertEqual(render.call_count, 1)
        self.assertEqual(first['X-Data-Cache'], 'MISS')
        self.assertEqual(repeated['X-Data-Cache'], 'HIT')
        self.assertEqual(first.content, repeated.content)
