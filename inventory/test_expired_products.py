"""Expired inventory API eligibility, visibility and cache regressions."""
from datetime import datetime, time, timedelta, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from config.test_support import APITestCase
from inventory.models import Category, Product, ProductBatch, StockAdjustment
from systemsetting.models import NotificationSettings


@override_settings(CACHES={'default': {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    'LOCATION': 'expired-products-tests',
}})
class ExpiredProductAPITests(APITestCase):
    url = '/inventory/expired/products/'

    def setUp(self):
        cache.clear()
        self.today = timezone.localdate()
        users = get_user_model().objects
        self.admin = users.create_user(
            username='expired-admin', email='expired-admin@example.test', role='admin')
        self.staff = users.create_user(
            username='expired-staff', email='expired-staff@example.test', role='staff')
        self.category = Category.objects.create(name='Expired inventory')
        self.product = Product.objects.create(
            name='Milk', category=self.category, unit='L', shelf_life=7)
        self.client.force_authenticate(self.admin)

    def batch(self, name, **overrides):
        fields = dict(
            product=self.product, batch_number=name, initial_quantity=10,
            remaining_quantity=5, unit_price=Decimal('20.00'),
            expiration_date=self.today - timedelta(days=1),
            date_received=self.today - timedelta(days=7),
        )
        fields.update(overrides)
        return ProductBatch.objects.create(**fields)

    def test_requires_authentication_and_is_read_only_for_both_roles(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)
        for user in (self.admin, self.staff):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.url).status_code, 200)
            for method in (self.client.post, self.client.patch, self.client.put, self.client.delete):
                with self.subTest(role=user.role, method=method.__name__):
                    self.assertEqual(method(self.url, {}, format='json').status_code, 405)

    def test_returns_only_received_expired_stock_in_expiration_order(self):
        past = self.batch('PAST-AVAILABLE')
        oldest = self.batch('PAST-EXPIRED', status='expired',
                            expiration_date=self.today - timedelta(days=3))
        same_date = self.batch('PAST-SAME-DATE')
        marked = self.batch('MARKED-EXPIRED', status='expired',
                            expiration_date=self.today + timedelta(days=1))
        for name, fields in (
            ('TODAY', {'expiration_date': self.today}),
            ('FUTURE', {'expiration_date': self.today + timedelta(days=1)}),
            ('EMPTY', {'remaining_quantity': 0}),
            ('DEPLETED', {'status': 'depleted'}),
            ('DISPOSED', {'status': 'disposed'}),
            ('UNRECEIVED', {'date_received': self.today + timedelta(days=1)}),
            ('HISTORICAL', {'is_historical_reference': True}),
        ):
            self.batch(name, **fields)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.data],
                         [oldest.pk, past.pk, same_date.pk, marked.pk])
        self.assertTrue(all(row['expiry_status'] == 'expired' for row in response.data))
        row = response.data[1]
        self.assertEqual(row['status'], 'available')
        self.assertEqual(row['product']['id'], self.product.pk)
        self.assertEqual(row['product']['category']['id'], self.category.pk)
        self.assertEqual(row['batch_number'], past.batch_number)
        self.assertEqual(row['expiration_date'], past.expiration_date.isoformat())
        self.assertEqual(Decimal(row['remaining_quantity']), Decimal('5'))
        past.refresh_from_db()
        self.assertEqual(past.status, 'available')
        self.assertFalse(StockAdjustment.objects.exists())

    def test_staff_visibility_is_enforced_while_admin_sees_all_expired_stock(self):
        visible = self.batch('VISIBLE')
        hidden_category = Category.objects.create(name='Private', is_visible_to_staff=False)
        inactive_category = Category.objects.create(name='Inactive', is_active=False)
        for name, fields in (
            ('PRIVATE', {'category': hidden_category}),
            ('INACTIVE-CATEGORY', {'category': inactive_category}),
            ('INACTIVE-PRODUCT', {'category': self.category, 'is_active': False}),
        ):
            product = Product.objects.create(name=name, unit='L', shelf_life=7, **fields)
            self.batch(name, product=product)

        self.assertEqual(len(self.client.get(self.url).data), 4)
        self.client.force_authenticate(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.data], [visible.pk])

    def test_disabled_notifications_do_not_hide_expired_inventory(self):
        batch = self.batch('NO-ALERTS')
        NotificationSettings.objects.create(near_expiry_alerts=False)
        for user in (self.admin, self.staff):
            self.client.force_authenticate(user)
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual([row['id'] for row in response.data], [batch.pk])

    def test_product_total_stock_still_counts_only_sellable_stock(self):
        self.batch('EXPIRED')
        self.batch('SELLABLE', remaining_quantity=3,
                   expiration_date=self.today + timedelta(days=7))
        response = self.client.get(self.url)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(Decimal(str(response.data[0]['product']['total_stock'])), Decimal('3'))

    def test_empty_inventory_returns_an_empty_array(self):
        self.assertEqual(self.client.get(self.url).data, [])

    def test_uses_business_local_date_when_utc_date_is_still_yesterday(self):
        batch = self.batch('LOCAL-DATE')
        utc_now = datetime.combine(self.today - timedelta(days=1), time(16, 30),
                                   tzinfo=datetime_timezone.utc)
        with timezone.override('Asia/Manila'), patch('django.utils.timezone.now', return_value=utc_now):
            response = self.client.get(self.url)
        self.assertEqual([row['id'] for row in response.data], [batch.pk])

    def test_cached_list_updates_when_business_date_advances(self):
        batch = self.batch('EXPIRES-TODAY', expiration_date=self.today)
        self.assertEqual(self.client.get(self.url).data, [])
        with patch('django.utils.timezone.localdate', return_value=self.today + timedelta(days=1)):
            response = self.client.get(self.url)
        self.assertEqual([row['id'] for row in response.data], [batch.pk])

    def test_cached_staff_list_cannot_keep_a_newly_hidden_category(self):
        batch = self.batch('HIDDEN-AFTER-READ')
        self.client.force_authenticate(self.staff)
        self.assertEqual([row['id'] for row in self.client.get(self.url).data], [batch.pk])
        # QuerySet.update bypasses invalidation signals, exercising the visibility digest.
        Category.objects.filter(pk=self.category.pk).update(is_visible_to_staff=False)
        self.assertEqual(self.client.get(self.url).data, [])

    def test_disposal_removes_batch_from_cached_lists_for_both_roles(self):
        batch = self.batch('DISPOSE')
        for user in (self.admin, self.staff):
            self.client.force_authenticate(user)
            self.assertEqual([row['id'] for row in self.client.get(self.url).data], [batch.pk])
        self.client.force_authenticate(self.admin)
        with self.capture_commit_callbacks(execute=True):
            response = self.client.post('/inventory/stock-adjustments/', {
                'product_batch_id': batch.pk, 'adjustment_type': 'expired',
                'quantity': '5', 'unit_cost': '20',
            }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        for user in (self.admin, self.staff):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.url).data, [])
