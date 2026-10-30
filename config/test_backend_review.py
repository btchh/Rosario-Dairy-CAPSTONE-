"""Regressions for batch edits, cached visibility and cancellation movements."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import OperationalError
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from config.test_support import APITestCase
from inventory.models import Category, Product, ProductBatch
from inventory.serializers import ProdBatchSerializer
from inventory.services.batch_service import BatchService
from reporting.services import inventory_ledger
from sales.models import Customer, Transaction, Order, StockReturn
from sales.services.order_service import place_order, void_fulfilled_order


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                                      'LOCATION': 'backend-review-regressions'}})
class BackendReviewRegressions(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(username='review-admin', email='review-admin@example.test', role='admin')
        self.staff = get_user_model().objects.create_user(username='review-staff', email='review-staff@example.test', role='staff')
        self.category = Category.objects.create(name='Review dairy')
        self.product = Product.objects.create(name='Review milk', category=self.category, unit='L', shelf_life=7)
        self.batch = ProductBatch.objects.create(product=self.product, batch_number='REVIEW-1', initial_quantity=10,
            remaining_quantity=10, unit_price=50, expiration_date=timezone.localdate()+timedelta(days=7))

    def test_stale_product_edit_keeps_stock_and_depleted_status(self):
        serializer = ProdBatchSerializer(ProductBatch.objects.get(pk=self.batch.pk), data={'notes': 'Metadata only'},
            partial=True, context={'request': SimpleNamespace(user=self.admin)})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        BatchService.deduct_product_batch(self.product, Decimal('10'))
        saved = serializer.save()
        self.batch.refresh_from_db()
        self.assertEqual((self.batch.remaining_quantity, self.batch.status), (0, 'depleted'))
        self.assertEqual(saved.remaining_quantity, 0)
        self.assertEqual(saved.notes, 'Metadata only')


    def test_metadata_dates_are_revalidated_against_current_batch(self):
        serializer = ProdBatchSerializer(ProductBatch.objects.get(pk=self.batch.pk),
            data={'expiration_date': (timezone.localdate()+timedelta(days=2)).isoformat()}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        ProductBatch.objects.filter(pk=self.batch.pk).update(date_received=timezone.localdate()+timedelta(days=3))
        with self.assertRaises(ValidationError):
            serializer.save()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.expiration_date, timezone.localdate()+timedelta(days=7))

    def test_staff_caches_cannot_revive_hidden_product_after_invalidation_outage(self):
        self.client.force_authenticate(self.staff)
        self.assertIn(self.product.pk, [r['id'] for r in self.client.get('/inventory/products/').data])
        first_report = self.client.get('/api/reports/preview/?type=inventory')
        self.assertEqual(len(first_report.data['data']['items']), 1)
        with patch('config.data_cache.cache.set', side_effect=ConnectionError('outage')), self.capture_commit_callbacks(execute=True):
            self.category.is_visible_to_staff = False
            self.category.save(update_fields=['is_visible_to_staff'])
        self.assertEqual(self.client.get(f'/inventory/products/{self.product.pk}/').status_code, 404)
        result = self.client.get('/inventory/products/')
        self.assertNotIn(self.product.pk, [r['id'] for r in result.data])
        report = self.client.get('/api/reports/preview/?type=inventory')
        self.assertEqual(report.data['data']['items'], [])

    def test_product_moved_into_private_category_is_removed_from_staff_cache(self):
        hidden = Category.objects.create(name='Private', is_visible_to_staff=False)
        self.client.force_authenticate(self.staff)
        self.client.get('/inventory/products/')
        with patch('config.data_cache.cache.set', side_effect=ConnectionError('outage')), self.capture_commit_callbacks(execute=True):
            self.product.category = hidden
            self.product.save(update_fields=['category'])
        self.assertEqual(self.client.get('/inventory/products/').data, [])

    def test_unverifiable_staff_visibility_returns_503_instead_of_cached_payload(self):
        self.client.force_authenticate(self.staff)
        self.client.get('/inventory/products/')
        with patch('config.data_cache.staff_visibility_version', side_effect=OperationalError('private database details')):
            response = self.client.get('/inventory/products/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private database details', str(response.data))
        self.assertNotIn(self.product.name, str(response.data))

    def order(self, *, yesterday=False, backdated=False):
        old = timezone.now()-timedelta(days=1)
        ProductBatch.objects.filter(pk=self.batch.pk).update(created_at=old)
        customer = Customer.objects.create(name='Review buyer', created_by=self.admin)
        order = place_order(customer, [(self.product, Decimal('2'), Decimal('50'))], self.admin, payment_method='online')
        if yesterday or backdated:
            values = {'created_at': old}
            if backdated:
                values['encoded_at'] = timezone.now()
            Transaction.objects.filter(pk=order.transaction_id).update(**values)
            Order.objects.filter(pk=order.pk).update(created_at=old)
        return order

    def ledger_row(self):
        return next(r for r in inventory_ledger()['rows'] if r['id'] == self.product.pk)

    def test_old_sale_cancelled_today_keeps_opening_and_records_actual_return(self):
        order = self.order(yesterday=True)
        self.assertEqual(self.ledger_row()['beginning'], 8)
        void_fulfilled_order(order)
        row = self.ledger_row()
        self.assertEqual((row['beginning'], row['ending'], row['adjustments']), (8, 10, -2))
        self.assertIn('2 returned from cancelled sale', row['remarks'])
        self.assertEqual(StockReturn.objects.get().quantity, 2)
        with self.assertRaises(ValueError):
            void_fulfilled_order(order)
        self.assertEqual(StockReturn.objects.count(), 1)

    def test_same_day_and_backdated_encoding_cancellations_do_not_double_credit(self):
        for backdated in (False, True):
            order = self.order(backdated=backdated)
            void_fulfilled_order(order)
            row = self.ledger_row()
            self.assertEqual(row['beginning'], 10)
            self.assertEqual(row['ending'], 10)
            self.assertEqual(sum(row['allocations'].values()), -row['adjustments'])

    def test_capped_return_records_only_quantity_actually_restored(self):
        order = self.order()
        ProductBatch.objects.filter(pk=self.batch.pk).update(remaining_quantity=9)
        void_fulfilled_order(order)
        self.assertEqual(StockReturn.objects.get().quantity, 1)
        self.assertEqual(self.ledger_row()['adjustments'], -1)

    def test_expired_batch_keeps_physical_deduction_without_return_credit(self):
        order = self.order()
        ProductBatch.objects.filter(pk=self.batch.pk).update(expiration_date=timezone.localdate()-timedelta(days=1))
        void_fulfilled_order(order)
        row = self.ledger_row()
        self.assertEqual(StockReturn.objects.get().quantity, 0)
        self.assertEqual((row['beginning'], row['ending'], row['adjustments']), (10, 8, 0))

    def test_cancellation_failure_rolls_back_stock_return_and_void_state(self):
        order = self.order()
        with patch.object(StockReturn.objects, 'create', side_effect=ValueError('return event failed')):
            with self.assertRaises(ValueError):
                void_fulfilled_order(order)
        self.batch.refresh_from_db()
        order.transaction.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 8)
        self.assertFalse(order.transaction.is_voided)
        self.assertFalse(StockReturn.objects.exists())
