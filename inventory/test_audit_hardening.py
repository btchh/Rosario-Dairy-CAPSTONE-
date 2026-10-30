"""Deployment audit regressions for validated inventory date boundaries."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase
from django.utils import timezone

from config.test_support import APITestCase
from inventory.models import Category, FEFOConf, Product, ProductBatch, StockAdjustment, StockCount
from inventory.serializers.fefoconf_serializer import FEFOConfSerializer
from inventory.services.eligibility import expiry_cutoff
from inventory.services.stock_check_service import check_product_expiration
from reporting.services import inventory_status
from sales.models import Customer, Transaction


class ExpiryBoundaryValidationTests(SimpleTestCase):
    def test_unrepresentable_near_and_critical_thresholds_are_rejected(self):
        serializer = FEFOConfSerializer(data={
            'near_expiry_threshold': 2147483647,
            'critical_expiry_threshold': 2147483647,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('near_expiry_threshold', serializer.errors)
        self.assertIn('critical_expiry_threshold', serializer.errors)

    def test_largest_representable_threshold_remains_valid(self):
        today = date(2026, 10, 27)
        remaining_days = (date.max - today).days
        with patch('inventory.serializers.fefoconf_serializer.timezone.localdate', return_value=today):
            serializer = FEFOConfSerializer(data={
                'near_expiry_threshold': remaining_days,
                'critical_expiry_threshold': remaining_days,
            })
            self.assertTrue(serializer.is_valid(), serializer.errors)
            too_large = FEFOConfSerializer(data={'near_expiry_threshold': remaining_days + 1})
            self.assertFalse(too_large.is_valid())

    def test_partial_updates_validate_effective_thresholds(self):
        config = FEFOConf(near_expiry_threshold=7, critical_expiry_threshold=3)
        valid = FEFOConfSerializer(config, data={'near_expiry_threshold': 10}, partial=True)
        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(valid.validated_data, {'near_expiry_threshold': 10})
        invalid = FEFOConfSerializer(config, data={'critical_expiry_threshold': 2147483647}, partial=True)
        self.assertFalse(invalid.is_valid())
        self.assertIn('critical_expiry_threshold', invalid.errors)

    def test_calendar_boundary_remains_safe_after_the_date_advances(self):
        today = date(2026, 10, 27)
        remaining_days = (date.max - today).days
        self.assertEqual(expiry_cutoff(today, remaining_days), date.max)
        self.assertEqual(expiry_cutoff(today + timedelta(days=1), remaining_days), date.max)
        self.assertEqual(expiry_cutoff(today, 2147483647), date.max)
        self.assertEqual(expiry_cutoff(today, 7), today + timedelta(days=7))
        self.assertEqual(expiry_cutoff(today, 0), today)


class ExpiryBoundaryAPITests(APITestCase):
    def setUp(self):
        cache.clear()
        users = get_user_model().objects
        self.admin = users.create_user(username='expiry-audit-admin', email='expiry-admin@example.com', password='test', role='admin')
        self.staff = users.create_user(username='expiry-audit-staff', email='expiry-staff@example.com', password='test', role='staff')
        self.client.force_authenticate(self.admin)
        self.config = FEFOConf.get_config()

    def test_both_admin_endpoints_reject_invalid_thresholds_without_saving(self):
        for url in ('/inventory/fefo-config/1/', '/settings/inventory/'):
            for field in ('near_expiry_threshold', 'critical_expiry_threshold'):
                with self.subTest(url=url, field=field):
                    response = self.client.patch(url, {field: 2147483647}, format='json')
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(field, response.data)
                    self.config.refresh_from_db()
                    self.assertEqual(self.config.near_expiry_threshold, 7)
                    self.assertEqual(self.config.critical_expiry_threshold, 3)

    def test_staff_cannot_change_thresholds(self):
        self.client.force_authenticate(self.staff)
        for url in ('/inventory/fefo-config/1/', '/settings/inventory/'):
            response = self.client.patch(url, {'near_expiry_threshold': 10}, format='json')
            self.assertEqual(response.status_code, 403)
        self.config.refresh_from_db()
        self.assertEqual(self.config.near_expiry_threshold, 7)

    def test_legacy_large_thresholds_preserve_alerts_and_inventory_reports(self):
        today = timezone.localdate()
        product = Product.objects.create(
            name='Expiry Boundary Milk', category=Category.objects.create(name='Expiry Boundary'),
            unit='liter', shelf_life=7,
        )
        for (model, relation) in ((ProductBatch, {'product': product}),):
            model.objects.create(
                **relation, batch_number=f'EXPIRY-{model.__name__}',
                initial_quantity=Decimal('1'), remaining_quantity=Decimal('1'),
                expiration_date=today + timedelta(days=30),
            )
        FEFOConf.objects.filter(pk=self.config.pk).update(
            near_expiry_threshold=2147483647, critical_expiry_threshold=2147483647,
        )
        products = list(check_product_expiration())
        self.assertEqual([row.product_id for row in products], [product.pk])
        self.assertEqual(products[0].expiry_status, 'critical')
        self.assertEqual(inventory_status()['expiring_soon_batch_count'], 1)
        for url in ('/inventory/expiring/products/',):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.data), 1)


class InventoryIdentifierValidationTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = get_user_model().objects.create_user(username='identifier-audit-staff', email='identifier-staff@example.com', password='test', role='staff')
        self.client.force_authenticate(self.staff)
        self.product = Product.objects.create(
            name='Identifier Milk', category=Category.objects.create(name='Identifier Audit'),
            unit='liter', shelf_life=7,
        )
        fields = dict(initial_quantity=Decimal('10'), remaining_quantity=Decimal('10'),
                      expiration_date=timezone.localdate() + timedelta(days=30), unit_price=Decimal('1'))
        self.product_batch = ProductBatch.objects.create(product=self.product, batch_number='IDENTIFIER-PRODUCT', **fields)
        self.customer = Customer.objects.create(name='Identifier Customer', created_by=self.staff)

    def test_invalid_batch_ids_never_deduct_or_reconcile_a_different_batch(self):
        for (field, batch) in (('product_batch_id', self.product_batch),):
            for url, payload in (
                ('/inventory/stock-adjustments/', {'adjustment_type': 'spoilage', 'quantity': '1', 'unit_cost': '1'}),
                ('/inventory/stock-counts/', {'counted_quantity': '9'}),
            ):
                for value in (batch.pk + 0.5, float(batch.pk), True, False, [], {}, 0, -1, 2**63, str(2**63)):
                    with self.subTest(field=field, url=url, value=value):
                        response = self.client.post(url, {**payload, field: value}, format='json')
                        self.assertEqual(response.status_code, 400)
        for batch in (self.product_batch,):
            batch.refresh_from_db()
            self.assertEqual(batch.remaining_quantity, Decimal('10'))
        self.assertEqual(StockAdjustment.objects.count(), 0)
        self.assertEqual(StockCount.objects.count(), 0)

    def test_numeric_string_ids_and_empty_optional_batch_ids_remain_supported(self):
        for (field, other_field, batch) in (('product_batch_id', 'ingredient_batch_id', self.product_batch),):
            response = self.client.post('/inventory/stock-adjustments/', {
                field: str(batch.pk), other_field: None, 'adjustment_type': 'spoilage',
                'quantity': '1', 'unit_cost': '1',
            }, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            response = self.client.post('/inventory/stock-counts/', {
                field: str(batch.pk), other_field: None, 'counted_quantity': '8',
            }, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            batch.refresh_from_db()
            self.assertEqual(batch.remaining_quantity, Decimal('8'))

    def test_invalid_optional_second_batch_id_is_not_silently_ignored(self):
        response = self.client.post('/inventory/stock-adjustments/', {
            'product_batch_id': self.product_batch.pk, 'ingredient_batch_id': False,
            'adjustment_type': 'spoilage', 'quantity': '1', 'unit_cost': '1',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(StockAdjustment.objects.count(), 0)

    def cart(self, **extra):
        return {
            'items': [{'product_id': self.product.pk, 'quantity': '1', 'unit_price': '1'}],
            'payment_method': 'cash', 'amount_tendered': '1', **extra,
        }

    def test_checkout_rejects_invalid_customer_ids_before_stock_changes(self):
        for value in (self.customer.pk + 0.5, float(self.customer.pk), True, False, [], {}, 0, -1, 2**63, str(2**63)):
            with self.subTest(value=value):
                response = self.client.post('/sales/checkout/', self.cart(customer_id=value), format='json')
                self.assertEqual(response.status_code, 400)
        self.product_batch.refresh_from_db()
        self.assertEqual(self.product_batch.remaining_quantity, Decimal('10'))
        self.assertEqual(Transaction.objects.count(), 0)

    def test_checkout_preserves_numeric_string_and_optional_customer_behavior(self):
        for extra in ({'customer_id': str(self.customer.pk)}, {}, {'customer_id': ''}, {'customer_id': None}):
            response = self.client.post('/sales/checkout/', self.cart(**extra), format='json')
            self.assertEqual(response.status_code, 201, response.data)
            expected = self.customer.pk if extra.get('customer_id') else None
            self.assertEqual(Transaction.objects.get(pk=response.data['id']).customer_id, expected)
