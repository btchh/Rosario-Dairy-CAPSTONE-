from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from config.data_cache import data_version
from inventory.models import Category, Product, ProductBatch
from reporting.services import get_report
from sales.models import Transaction


class DataCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(
            username='cache-admin', email='cache-admin@example.com', role='admin',
        )
        self.staff = get_user_model().objects.create_user(
            username='cache-staff', email='cache-staff@example.com', role='staff',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.category = Category.objects.create(name='Visible')
        self.product = Product.objects.create(
            name='Milk', category=self.category, unit='L',  shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='CACHE-001', initial_quantity=10,
            remaining_quantity=10, expiration_date=timezone.localdate() + timedelta(days=7),
        )

    def test_repeated_lists_and_dashboard_aggregates_avoid_data_queries(self):
        endpoints = [
            '/inventory/products/', '/inventory/ingredients/',
            '/inventory/product-batches/', '/inventory/ingredient-batches/',
            '/inventory/categories/', '/inventory/low-stock/products/',
            '/sales/transactions/', '/sales/orders/', '/sales/customers/',
            '/sales/reports/revenue/', '/sales/reports/best-sellers/',
            '/sales/reports/sales-by-category/', '/api/forecasting/forecast/',
        ]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                first = self.client.get(endpoint)
                self.assertEqual(first.status_code, 200, first.data)
                # Some handlers create singleton configuration on first use.
                self.client.get(endpoint)
                with self.assertNumQueries(0):
                    repeated = self.client.get(endpoint)
                self.assertEqual(first.data, repeated.data)

    def test_commit_invalidates_stock_and_report_payloads(self):
        endpoint = '/inventory/products/'
        first = self.client.get(endpoint)
        self.assertEqual(Decimal(first.data[0]['total_stock']), 10)
        get_report('inventory')
        with self.captureOnCommitCallbacks(execute=True):
            self.batch.remaining_quantity = 3
            self.batch.save()
        refreshed = self.client.get(endpoint)
        self.assertEqual(Decimal(refreshed.data[0]['total_stock']), 3)
        self.assertEqual(get_report('inventory')['items'][0]['quantity'], Decimal('3'))

    def test_rollback_does_not_invalidate(self):
        version = data_version()
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    self.product.name = 'Rolled back'
                    self.product.save()
                    raise ValueError('rollback')
            except ValueError:
                pass
        self.assertEqual(data_version(), version)

    def test_staff_scope_and_permissions_are_checked_on_warm_cache(self):
        self.category.is_visible_to_staff = False
        self.category.save()
        admin = self.client.get('/inventory/products/')
        self.assertEqual(len(admin.data), 1)
        self.client.force_authenticate(self.staff)
        staff = self.client.get('/inventory/products/')
        self.assertEqual(staff.data, [])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/inventory/products/').status_code, 401)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get('/sales/reports/revenue/').status_code, 403)

    def test_filters_and_pages_have_separate_keys(self):
        for amount in (10, 20):
            Transaction.objects.create(handled_by=self.admin, subtotal=amount, total_amount=amount)
        first = self.client.get('/sales/transactions/?page_size=1')
        self.assertEqual(first.status_code, 200)
        second = self.client.get('/sales/transactions/?page_size=1&page=2')
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(first.data['results'][0]['id'], second.data['results'][0]['id'])
        self.assertEqual(self.client.get('/sales/transactions/?payment_method=invalid').status_code, 400)
        self.assertEqual(self.client.get('/sales/transactions/?page_size=1&page=3').status_code, 404)

    def test_deletion_invalidates_cached_catalog(self):
        self.client.get('/inventory/products/')
        with self.captureOnCommitCallbacks(execute=True):
            self.batch.delete()
            self.product.delete()
        self.assertEqual(self.client.get('/inventory/products/').data, [])

    def test_category_visibility_change_invalidates_staff_cache(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(len(self.client.get('/inventory/products/').data), 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.category.is_visible_to_staff = False
            self.category.save()
        self.assertEqual(self.client.get('/inventory/products/').data, [])

    def test_checkout_invalidates_history_and_stock_after_commit(self):
        self.assertEqual(self.client.get('/sales/transactions/').data['count'], 0)
        self.client.get('/inventory/products/')
        with self.captureOnCommitCallbacks(execute=True):
            sale = self.client.post('/sales/checkout/', {
                'items': [{'product_id': self.product.pk, 'quantity': '2.00', 'unit_price': '50.00'}],
                'payment_method': 'cash', 'amount_tendered': '100.00',
            }, format='json')
        self.assertEqual(sale.status_code, 201, sale.data)
        self.assertEqual(self.client.get('/sales/transactions/').data['count'], 1)
        self.assertEqual(Decimal(self.client.get('/inventory/products/').data[0]['total_stock']), 8)

    def test_cache_failure_still_returns_database_data(self):
        with patch('config.data_cache.cache.get', side_effect=ConnectionError), \
             patch('config.data_cache.cache.set', side_effect=ConnectionError):
            response = self.client.get('/inventory/products/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['name'], 'Milk')

    def test_forecast_cache_separates_periods_and_supports_force_refresh(self):
        with patch('reporting.services.sarima_forecast', side_effect=lambda period: {'period': period}) as build:
            self.assertEqual(get_report('sarima_forecast', period='weekly'), {'period': 'weekly'})
            get_report('sarima_forecast', period='weekly')
            get_report('sarima_forecast', period='monthly')
            self.assertEqual(build.call_count, 2)
            get_report('sarima_forecast', force_refresh=True, period='weekly')
            self.assertEqual(build.call_count, 3)

    def test_date_rollover_does_not_reuse_yesterdays_payload(self):
        with patch('config.data_cache.timezone.localdate', return_value=timezone.localdate() - timedelta(days=1)):
            self.client.get('/inventory/products/')
        with patch('inventory.views.product_views.ProductViewSet.get_queryset', side_effect=RuntimeError('new read')):
            with self.assertRaisesMessage(RuntimeError, 'new read'):
                self.client.get('/inventory/products/')

    def test_manual_report_refresh_invalidates_api_cache(self):
        self.client.get('/inventory/products/')
        old_version = data_version()
        with self.captureOnCommitCallbacks(execute=True), \
             patch('reporting.views.refresh_reports', return_value=(timezone.now(), {})):
            response = self.client.post('/api/reports/refresh/')
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(data_version(), old_version)
