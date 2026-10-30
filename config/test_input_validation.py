"""Invalid API inputs must fail before catalog, sales or stock mutations."""
from copy import deepcopy
from datetime import timedelta
import json
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from config.test_support import APITestCase
from inventory.models import Category, Product, ProductBatch, StockAdjustment, StockCount
from sales.models import Customer, Order, SaleSubmission, Transaction


@override_settings(CACHES={'default': {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    'LOCATION': 'input-validation-regressions',
}})
class InputValidationTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(
            username='input-admin', email='input-admin@example.test', role='admin')
        self.client.force_authenticate(self.admin)
        self.category = Category.objects.create(name='Input dairy')
        self.product = Product.objects.create(
            name='Input milk', category=self.category, unit='L', shelf_life=7)
        self.customer = Customer.objects.create(name='Input customer', created_by=self.admin)
        self.expiry = (timezone.localdate() + timedelta(days=7)).isoformat()
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='INPUT-1', initial_quantity=10,
            remaining_quantity=10, unit_price=50, expiration_date=self.expiry)

    def sale(self, **extra):
        return {'items': [{'product_id': self.product.pk, 'quantity': '1', 'unit_price': '50'}],
                'payment_method': 'online', **extra}

    def relationships(self):
        return [('/inventory/products/', 'category_id', self.category.pk, {'name': 'New milk', 'unit': 'L', 'shelf_life': 7, 'category_id': self.category.pk}), ('/inventory/product-batches/', 'product_id', self.product.pk, {'product_id': self.product.pk, 'quantity': '5', 'expiration_date': self.expiry}), ('/sales/orders/', 'customer_id', self.customer.pk, self.sale(customer_id=self.customer.pk)), ('/sales/encoding/', 'customer_id', self.customer.pk, self.sale(customer_id=self.customer.pk, request_id=str(uuid4()), transaction_date=timezone.localdate().isoformat()))]

    def snapshot(self):
        self.batch.refresh_from_db()
        return (tuple(model.objects.count() for model in (
            Product, ProductBatch, Order, Transaction, SaleSubmission,
            StockAdjustment, StockCount)), self.batch.remaining_quantity)

    def assert_json_error(self, response, field=None):
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response['Content-Type'], 'application/json')
        if field:
            self.assertIn(field, response.data)

    def test_invalid_relationship_ids_cannot_create_records_or_deduct_stock(self):
        before = self.snapshot()
        for route, field, pk, valid in self.relationships():
            for value in (pk + 0.9, float(pk), True, False, [], {}, 0, -1,
                          9223372036854775808, '1.9', 'bad'):
                with self.subTest(route=route, field=field, value=value):
                    payload = deepcopy(valid)
                    payload[field] = value
                    self.assert_json_error(self.client.post(route, payload, format='json'), field)
                    self.assertEqual(self.snapshot(), before)

    def test_relationships_preserve_integer_and_integer_string_ids(self):
        for route, field, pk, valid in self.relationships():
            for value in (pk, str(pk)):
                with self.subTest(route=route, field=field, value=value):
                    payload = deepcopy(valid)
                    payload[field] = value
                    if route == '/inventory/products/':
                        payload['name'] = f'New milk {type(value).__name__}'
                    if route == '/sales/encoding/':
                        payload['request_id'] = str(uuid4())
                    response = self.client.post(route, payload, format='json')
                    self.assertEqual(response.status_code, 201, response.data)
                    related = {'category_id': 'category', 'product_id': 'product',
                               'customer_id': 'customer'}.get(field)
                    self.assertEqual(response.data[related]['id'] if related
                                     else response.data[field], pk)

    def test_partial_updates_reject_fractional_relationships_without_changes(self):
        for (route, field, pk) in ((f'/inventory/products/{self.product.pk}/', 'category_id', self.category.pk), (f'/inventory/product-batches/{self.batch.pk}/', 'product_id', self.product.pk)):
            with self.subTest(route=route, field=field):
                self.assert_json_error(self.client.patch(route, {field: pk + 0.9}, format='json'), field)
        self.product.refresh_from_db()
        self.assertEqual(self.product.category_id, self.category.pk)

    def test_nonobject_catalog_bodies_return_json_errors_on_create_and_update(self):
        before = self.snapshot()
        for (route, pk) in (('/inventory/products/', self.product.pk),):
            for method, url in (('post', route), ('put', f'{route}{pk}/'), ('patch', f'{route}{pk}/')):
                for body in (True, False, 42, 0, [], [1], 'text', None):
                    with self.subTest(method=method, route=route, body=body):
                        response = self.client.generic(method.upper(), url, json.dumps(body),
                                                       content_type='application/json')
                        self.assert_json_error(response)
        self.assertEqual(self.snapshot(), before)

    def test_nontext_request_ids_fail_before_sales_or_retry_keys_are_created(self):
        before = self.snapshot()
        for route, valid in (
            ('/sales/checkout/', self.sale()),
            ('/sales/orders/', self.sale(customer_id=self.customer.pk)),
            ('/sales/encoding/', self.sale(transaction_date=timezone.localdate().isoformat())),
        ):
            for key in (True, False, 0, 1, -1, [], {}, 1.5, 'not-a-uuid'):
                with self.subTest(route=route, key=key):
                    self.assert_json_error(self.client.post(route, {**valid, 'request_id': key}, format='json'))
                    self.assertEqual(self.snapshot(), before)

    def test_uuid_keys_preserve_replay_and_conflict_behavior(self):
        for route, extra in (
            ('/sales/checkout/', {}),
            ('/sales/orders/', {'customer_id': self.customer.pk}),
            ('/sales/encoding/', {'transaction_date': timezone.localdate().isoformat()}),
        ):
            with self.subTest(route=route):
                key = str(uuid4())
                payload = self.sale(request_id=key, **extra)
                first = self.client.post(route, payload, format='json', HTTP_IDEMPOTENCY_KEY=key)
                self.assertEqual(first.status_code, 201, first.data)
                after_first = self.snapshot()
                replay = self.client.post(route, payload, format='json', HTTP_IDEMPOTENCY_KEY=key)
                self.assertEqual(replay.status_code, 200, replay.data)
                self.assertEqual(replay.data['id'], first.data['id'])
                changed = deepcopy(payload)
                changed['items'][0]['quantity'] = '2'
                self.assertEqual(self.client.post(route, changed, format='json').status_code, 409)
                self.assertEqual(self.snapshot(), after_first)

    def test_optional_null_relationships_and_unkeyed_checkout_remain_supported(self):
        checkout = self.client.post('/sales/checkout/', self.sale(request_id=None, customer_id=None), format='json')
        self.assertEqual(checkout.status_code, 201, checkout.data)
        self.assertIsNone(checkout.data['customer'])
        encoded = self.client.post('/sales/encoding/', self.sale(
            request_id=str(uuid4()), customer_id=None,
            transaction_date=timezone.localdate().isoformat()), format='json')
        self.assertEqual(encoded.status_code, 201, encoded.data)
        self.assertIsNone(encoded.data['customer'])

    def test_invalid_report_limits_are_rejected_before_running_the_report(self):
        with patch('sales.views.report_views.SalesService.get_best_sellers') as report:
            for limit in ('0', '-1', 'bad', '1.5', '501', '9223372036854775808', '9' * 5000):
                with self.subTest(limit=limit[:30]):
                    response = self.client.get('/sales/reports/best-sellers/', {'limit': limit})
                    self.assert_json_error(response, 'error')
            report.assert_not_called()

    def test_reactivation_rejects_malformed_path_ids_with_json_without_changes(self):
        before = self.snapshot()
        models = (Product, Category)
        active_before = [list(model.objects.values_list('pk', 'is_active')) for model in models]
        for route in ('products', 'categories'):
            for value in ('bad', '1e3', '0', '-1', str(2**63), '9' * 5000):
                with self.subTest(route=route, value=value[:30]):
                    response = self.client.post(f'/inventory/{route}/{value}/reactivate/', {}, format='json')
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response['Content-Type'], 'application/json')
                    self.assertIn('error', response.data)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([list(model.objects.values_list('pk', 'is_active')) for model in models], active_before)

    def test_reactivation_preserves_valid_numeric_path_ids(self):
        for (route, obj) in (('products', self.product), ('categories', self.category)):
            with self.subTest(route=route):
                type(obj).objects.filter(pk=obj.pk).update(is_active=False)
                response = self.client.post(f'/inventory/{route}/{obj.pk}/reactivate/', {}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                obj.refresh_from_db()
                self.assertTrue(obj.is_active)

    def test_sales_filter_ids_reject_invalid_ranges_without_writes(self):
        before = self.snapshot()
        for route, field in (('/sales/orders/', 'customer_id'),
                             ('/sales/transactions/', 'customer_id'),
                             ('/sales/transactions/', 'handled_by')):
            for value in ('bad', '1.5', '0', '-1', str(2**63), '9' * 5000):
                with self.subTest(route=route, field=field, value=value[:30]):
                    self.assert_json_error(self.client.get(route, {field: value}), 'error' if 'transactions' in route else field)
        self.assertEqual(self.snapshot(), before)

    def test_sales_filter_ids_preserve_valid_numeric_strings(self):
        for route, field, pk in (('/sales/orders/', 'customer_id', self.customer.pk),
                                 ('/sales/transactions/', 'customer_id', self.customer.pk),
                                 ('/sales/transactions/', 'handled_by', self.admin.pk)):
            with self.subTest(route=route, field=field):
                response = self.client.get(route, {field: str(pk)})
                self.assertEqual(response.status_code, 200, response.data)

    def test_default_and_maximum_report_limits_remain_supported(self):
        for query in ('', '?limit=1', '?limit=500'):
            with self.subTest(query=query):
                response = self.client.get(f'/sales/reports/best-sellers/{query}')
                self.assertEqual(response.status_code, 200, response.data)

    def test_inventory_audit_text_rejects_json_containers_and_scalars_without_writes(self):
        before = self.snapshot()
        for route, field, valid in (
            ('/inventory/stock-adjustments/', 'reason', {
                'product_batch_id': self.batch.pk, 'adjustment_type': 'spillage',
                'quantity': '1', 'unit_cost': '50'}),
            ('/inventory/stock-counts/', 'notes', {
                'product_batch_id': self.batch.pk, 'counted_quantity': '9'}),
        ):
            for value in ([], {'unexpected': 'object'}, True, False, 42, 1.5):
                with self.subTest(route=route, value=value):
                    self.assert_json_error(self.client.post(route, {**valid, field: value}, format='json'))
                    self.assertEqual(self.snapshot(), before)

    def test_inventory_audit_text_preserves_strings_blank_and_null(self):
        for value in ('Checked today', '', None):
            with self.subTest(value=value):
                count = self.client.post('/inventory/stock-counts/', {
                    'product_batch_id': self.batch.pk, 'counted_quantity': '10', 'notes': value}, format='json')
                self.assertEqual(count.status_code, 201, count.data)
                self.assertEqual(StockCount.objects.get(pk=count.data['id']).notes, value)
                adjustment = self.client.post('/inventory/stock-adjustments/', {
                    'product_batch_id': self.batch.pk, 'adjustment_type': 'spillage',
                    'quantity': '1', 'unit_cost': '50', 'reason': value}, format='json')
                self.assertEqual(adjustment.status_code, 201, adjustment.data)
                self.assertEqual(StockAdjustment.objects.get(pk=adjustment.data['id']).reason, value)
                ProductBatch.objects.filter(pk=self.batch.pk).update(remaining_quantity=10)
