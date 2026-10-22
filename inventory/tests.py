"""Inventory tests: catalog, batches, FEFO, adjustments, visibility and outflow records.

Run all app tests: python manage.py test inventory
Run one class: python manage.py test inventory.tests.<TestClass>
PostgreSQL is required for the concurrency and database-locking checks.
"""
# Domain terms and deliberately artificial test fixture names.
# cSpell:words FEFO YYMM TMCML ingredientbatch productbatch testpass
# cSpell:ignore deleteadmin deletestaff visibilitystaff visibilityadmin stockalertadmin
# cSpell:ignore inventoryauditadmin iconadmin iconstaff outflowstaff mergeimport

import threading
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, connections, transaction
from django.utils import timezone

from config.test_support import (
    APIClient, APITestCase as TestCase, APITransactionTestCase as TransactionTestCase,
)

from config.data_cache import data_version
from forecasting.data import load_snapshot
from inventory.models import (
    BatchSequence,
    Category,
    FEFOConf,
    Ingredient,
    IngredientBatch,
    Product,
    ProductBatch,
    StockAdjustment,
    StockCount,
    Supplier,
)
from inventory.serializers import (
    IngBatchSerializer,
    IngredientSerializer,
    ProdBatchSerializer,
    ProductSerializer,
)
from inventory.services.batch_sequence_service import next_sequence
from inventory.services.batch_service import BatchService
from inventory.views.ingredient_views import IngredientViewSet
from inventory.views.ingredientbatch_views import IngredientBatchViewSet
from inventory.views.product_views import ProductViewSet
from inventory.views.productbatch_views import ProductBatchViewSet
from sales.models import Order, Transaction
from sales.models import Customer, OrderItem, TransactionItem
from inventory.services.product_consolidation import (
    consolidation_plan, consolidate_products, product_name_key,
)


User = get_user_model()


def make_user(username='staffuser', role='staff'):
    return User.objects.create_user( # type: ignore
        username=username,
        password='testpass123!',
        email=f'{username}@example.com',
        role=role,
        first_name='Test',
        last_name='User',
    )


# Core application behavior
# ----------------------------------------------------------------------------

class ConditionalDeleteAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_user('deleteadmin', role='admin')
        self.client.force_authenticate(user=self.admin)

    def test_unused_category_is_permanently_deleted(self):
        category = Category.objects.create(name='Unused')
        response = self.client.delete(f'/inventory/categories/{category.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['deletion_type'], 'permanent')
        self.assertFalse(Category.objects.filter(pk=category.pk).exists())

    def test_category_with_product_is_deactivated_and_can_be_reactivated(self):
        category = Category.objects.create(name='Linked')
        product = Product.objects.create(
            category=category, name='Linked Product', unit='piece',
            shelf_life=7,
        )
        response = self.client.delete(f'/inventory/categories/{category.pk}/')
        self.assertEqual(response.data['deletion_type'], 'deactivated')
        category.refresh_from_db()
        self.assertFalse(category.is_active)
        self.assertEqual(Product.objects.get(pk=product.pk).category.pk, category.pk)
        self.assertNotIn(category.pk, [row['id'] for row in self.client.get('/inventory/categories/').data])
        self.assertEqual(self.client.post(f'/inventory/categories/{category.pk}/reactivate/').status_code, 200)
        category.refresh_from_db()
        self.assertTrue(category.is_active)

    def test_explicit_deactivation_does_not_delete_unused_category(self):
        category = Category.objects.create(name='Keep for later')
        response = self.client.post(f'/inventory/categories/{category.pk}/deactivate/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Category.objects.get(pk=category.pk).is_active)

    def test_product_ingredient_and_supplier_follow_same_protection_rule(self):
        category = Category.objects.create(name='Products')
        unused = Product.objects.create(
            category=category, name='Unused', unit='piece',
            shelf_life=7,
        )
        linked = Product.objects.create(
            category=category, name='Linked', unit='piece',
            shelf_life=7,
        )
        ProductBatch.objects.create(
            product=linked, batch_number='DELETE-LINKED-PRODUCT',
            initial_quantity=Decimal('1.00'), remaining_quantity=Decimal('1.00'),
            expiration_date='2026-12-31',
        )
        self.assertEqual(self.client.delete(f'/inventory/products/{unused.pk}/').data['deletion_type'], 'permanent')
        self.assertFalse(Product.objects.filter(pk=unused.pk).exists())
        self.assertEqual(self.client.delete(f'/inventory/products/{linked.pk}/').data['deletion_type'], 'deactivated')
        self.assertFalse(Product.objects.get(pk=linked.pk).is_active)

        unused_ingredient = Ingredient.objects.create(
            name='Unused Ingredient', unit='piece', unit_price=Decimal('1.00'), shelf_life=7,
        )
        linked_ingredient = Ingredient.objects.create(
            name='Linked Ingredient', unit='piece', unit_price=Decimal('1.00'), shelf_life=7,
        )
        unused_supplier = Supplier.objects.create(name='Unused Supplier')
        linked_supplier = Supplier.objects.create(name='Linked Supplier')
        IngredientBatch.objects.create(
            ingredient=linked_ingredient, supplier=linked_supplier,
            batch_number='DELETE-LINKED-INGREDIENT',
            initial_quantity=Decimal('1.00'), remaining_quantity=Decimal('1.00'),
            expiration_date='2026-12-31',
        )
        self.assertEqual(self.client.delete(f'/inventory/ingredients/{unused_ingredient.pk}/').data['deletion_type'], 'permanent')
        self.assertEqual(self.client.delete(f'/inventory/ingredients/{linked_ingredient.pk}/').data['deletion_type'], 'deactivated')
        self.assertFalse(Ingredient.objects.get(pk=linked_ingredient.pk).is_active)
        self.assertEqual(self.client.delete(f'/inventory/suppliers/{unused_supplier.pk}/').data['deletion_type'], 'permanent')
        self.assertEqual(self.client.delete(f'/inventory/suppliers/{linked_supplier.pk}/').data['deletion_type'], 'deactivated')
        self.assertFalse(Supplier.objects.get(pk=linked_supplier.pk).is_active)

    def test_staff_can_deactivate_but_cannot_permanently_delete_ingredient(self):
        ingredient = Ingredient.objects.create(
            name='Staff Ingredient', unit='piece', unit_price=Decimal('1.00'), shelf_life=7,
        )
        self.client.force_authenticate(user=make_user('deletestaff'))
        self.assertEqual(self.client.delete(f'/inventory/ingredients/{ingredient.pk}/').status_code, 403)
        self.assertTrue(Ingredient.objects.get(pk=ingredient.pk).is_active)
        self.assertEqual(self.client.post(f'/inventory/ingredients/{ingredient.pk}/deactivate/').status_code, 200)
        self.assertFalse(Ingredient.objects.get(pk=ingredient.pk).is_active)


class StaffCategoryVisibilityAPITests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = APIClient()
        self.staff = make_user('visibilitystaff')
        self.admin = make_user('visibilityadmin', role='admin')
        self.visible_category = Category.objects.create(name='Visible')
        self.hidden_category = Category.objects.create(
            name='Hidden', is_visible_to_staff=False
        )
        self.visible_product = Product.objects.create(
            category=self.visible_category, name='Visible Product', unit='piece',
            shelf_life=7,
        )
        self.hidden_product = Product.objects.create(
            category=self.hidden_category, name='Hidden Product', unit='piece',
            shelf_life=7,
        )
        self.hidden_batch = ProductBatch.objects.create(
            product=self.hidden_product, batch_number='PRD-HIDDEN-001',
            unit_price=Decimal('10.00'), initial_quantity=Decimal('10.00'),
            remaining_quantity=Decimal('10.00'), expiration_date='2026-12-31',
        )

    def test_new_categories_are_visible_to_staff_by_default(self):
        self.assertTrue(self.visible_category.is_visible_to_staff)

    def test_staff_lists_and_retrieves_only_visible_categories(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/inventory/categories/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data], [self.visible_category.pk])
        response = self.client.get(f'/inventory/categories/{self.hidden_category.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_admin_can_see_and_toggle_hidden_category(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f'/inventory/categories/{self.hidden_category.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['is_visible_to_staff'])
        response = self.client.patch(
            f'/inventory/categories/{self.hidden_category.pk}/',
            {'is_visible_to_staff': True}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.hidden_category.refresh_from_db()
        self.assertTrue(self.hidden_category.is_visible_to_staff)

    def test_staff_cannot_toggle_or_manage_categories_and_products(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.patch(
            f'/inventory/categories/{self.visible_category.pk}/',
            {'is_visible_to_staff': False}, format='json',
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.delete(f'/inventory/products/{self.visible_product.pk}/')
        self.assertEqual(response.status_code, 403)

    def test_hidden_products_and_batches_are_not_exposed_to_staff(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/inventory/products/')
        self.assertEqual([item['id'] for item in response.data], [self.visible_product.pk])
        response = self.client.get(f'/inventory/products/{self.hidden_product.pk}/')
        self.assertEqual(response.status_code, 404)
        response = self.client.get(f'/inventory/product-batches/{self.hidden_batch.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_staff_cannot_create_batch_for_hidden_product(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.post('/inventory/product-batches/', {
            'product_id': self.hidden_product.pk,
            'quantity': '2.00',
            'expiration_date': '2026-12-31',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('product_id', response.data)

    def test_inactive_category_hides_products_and_batches_from_staff(self):
        self.visible_category.is_active = False
        self.visible_category.save(update_fields=['is_active'])
        batch = ProductBatch.objects.create(
            product=self.visible_product, batch_number='PRD-INACTIVE-CATEGORY',
            unit_price=Decimal('10.00'), initial_quantity=Decimal('5.00'),
            remaining_quantity=Decimal('5.00'), expiration_date='2026-12-31',
        )
        self.client.force_authenticate(user=self.staff)
        self.assertEqual(self.client.get('/inventory/categories/').data, [])
        self.assertEqual(self.client.get(f'/inventory/categories/{self.visible_category.pk}/').status_code, 404)
        self.assertEqual(self.client.get('/inventory/products/').data, [])
        self.assertEqual(self.client.get(f'/inventory/products/{self.visible_product.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/inventory/product-batches/{batch.pk}/').status_code, 404)
        create = self.client.post('/inventory/product-batches/', {
            'product_id': self.visible_product.pk,
            'quantity': '2.00', 'expiration_date': '2026-12-31',
        }, format='json')
        self.assertEqual(create.status_code, 400)
        self.assertIn('product_id', create.data)

    def test_inactive_product_batch_is_not_available_to_staff(self):
        batch = ProductBatch.objects.create(
            product=self.visible_product, batch_number='PRD-INACTIVE-PRODUCT',
            unit_price=Decimal('10.00'), initial_quantity=Decimal('5.00'),
            remaining_quantity=Decimal('5.00'), expiration_date='2026-12-31',
        )
        self.visible_product.is_active = False
        self.visible_product.save(update_fields=['is_active'])
        self.client.force_authenticate(user=self.staff)
        self.assertEqual(self.client.get(f'/inventory/product-batches/{batch.pk}/').status_code, 404)
        create = self.client.post('/inventory/product-batches/', {
            'product_id': self.visible_product.pk,
            'quantity': '2.00', 'expiration_date': '2026-12-31',
        }, format='json')
        self.assertEqual(create.status_code, 400)

    def test_admin_cannot_create_product_in_inactive_category(self):
        self.visible_category.is_active = False
        self.visible_category.save(update_fields=['is_active'])
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/inventory/products/', {
            'name': 'New Product', 'unit': 'piece', 'unit_price': '10.00',
            'shelf_life': 7, 'category_id': self.visible_category.pk,
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('category_id', response.data)

    def test_admin_cannot_restock_inactive_product(self):
        self.visible_product.is_active = False
        self.visible_product.save(update_fields=['is_active'])
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/inventory/product-batches/', {
            'product_id': self.visible_product.pk,
            'quantity': '2.00', 'expiration_date': '2026-12-31',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('product_id', response.data)

    def test_staff_cannot_adjust_or_count_stock_in_inactive_category(self):
        batch = ProductBatch.objects.create(
            product=self.visible_product, batch_number='PRD-INACTIVE-ADJUST',
            unit_price=Decimal('10.00'), initial_quantity=Decimal('5.00'),
            remaining_quantity=Decimal('5.00'), expiration_date='2026-12-31',
        )
        self.visible_category.is_active = False
        self.visible_category.save(update_fields=['is_active'])
        self.client.force_authenticate(user=self.staff)
        adjustment = self.client.post('/inventory/stock-adjustments/', {
            'product_batch_id': batch.pk, 'adjustment_type': 'spoilage',
            'quantity': '1.00', 'unit_cost': '10.00',
        }, format='json')
        self.assertEqual(adjustment.status_code, 400)
        count = self.client.post('/inventory/stock-counts/', {
            'product_batch_id': batch.pk, 'counted_quantity': '4.00',
        }, format='json')
        self.assertEqual(count.status_code, 400)
        batch.refresh_from_db()
        self.assertEqual(batch.remaining_quantity, Decimal('5.00'))


class StockAlertQueryTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.admin = make_user('stockalertadmin', role='admin')
        self.category = Category.objects.create(name='Stock Alerts')
        FEFOConf.objects.create(
            near_expiry_threshold=7,
            critical_expiry_threshold=3,
            low_stock_threshold=15,
        )

    def test_product_stock_check_uses_one_aggregate_query(self):
        for index in range(3):
            product = Product.objects.create(
                category=self.category, name=f'Product {index}', unit='piece',
                shelf_life=7,
            )
            ProductBatch.objects.create(
                product=product, batch_number=f'PRD-STOCK-{index}',
                unit_price=Decimal('10.00'), initial_quantity=Decimal('5.00'),
                remaining_quantity=Decimal('5.00'),
                expiration_date=self.today + timedelta(days=30),
            )

        with self.assertNumQueries(2):
            rows = BatchService.check_product_stock()

        self.assertEqual(len(rows), 3)

    def test_ingredient_stock_check_uses_one_aggregate_query(self):
        for index in range(3):
            ingredient = Ingredient.objects.create(
                name=f'Ingredient {index}', unit='liter',
                unit_price=Decimal('10.00'), shelf_life=7,
            )
            IngredientBatch.objects.create(
                ingredient=ingredient, batch_number=f'ING-STOCK-{index}',
                unit_price=Decimal('10.00'), initial_quantity=Decimal('5.00'),
                remaining_quantity=Decimal('5.00'),
                expiration_date=self.today + timedelta(days=30),
            )

        with self.assertNumQueries(2):
            rows = BatchService.check_ingredient_stock()

        self.assertEqual(len(rows), 3)

    def test_expiration_check_excludes_expired_and_labels_urgency(self):
        product = Product.objects.create(
            category=self.category, name='Expiry Product', unit='piece',
            shelf_life=7,
        )
        for suffix, offset in (
            ('EXPIRED', -1), ('CRITICAL', 2), ('NEAR', 5), ('LATER', 8),
        ):
            ProductBatch.objects.create(
                product=product, batch_number=f'PRD-{suffix}',
                unit_price=Decimal('10.00'), initial_quantity=Decimal('5.00'),
                remaining_quantity=Decimal('5.00'),
                expiration_date=self.today + timedelta(days=offset),
            )

        rows = list(BatchService.check_product_expiration())

        self.assertEqual(
            [(row.batch_number, getattr(row, 'expiry_status')) for row in rows],
            [('PRD-CRITICAL', 'critical'), ('PRD-NEAR', 'near_expiry')],
        )
        response = APIClient()
        response.force_authenticate(user=self.admin)
        payload = response.get('/inventory/expiring/products/')
        self.assertEqual(payload.status_code, 200)
        self.assertEqual(
            [item['expiry_status'] for item in payload.data],
            ['critical', 'near_expiry'],
        )

    def test_fefo_config_rejects_critical_window_larger_than_near_window(self):
        client = APIClient()
        client.force_authenticate(user=self.admin)
        response = client.patch(
            '/inventory/fefo-config/1/',
            {'near_expiry_threshold': 3, 'critical_expiry_threshold': 5},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('critical_expiry_threshold', response.data)


class CreateStockAdjustmentTests(TestCase):
    """Covers: race-condition fix (lock/re-fetch), correction upper-bound cap."""

    def setUp(self):
        self.user = make_user()
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-001',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('100.00'),
            remaining_quantity=Decimal('100.00'),
            expiration_date=timezone.localdate() + timedelta(days=30),
        )

    def test_rejects_zero_quantity_for_non_correction(self):
        with self.assertRaises(ValueError):
            BatchService.create_stock_adjustment(
                adjustment_type='spoilage', quantity=Decimal('0.00'),
                unit_cost=Decimal('10.00'), adjusted_by=self.user,
                product_batch=self.batch,
            )

    def test_rejects_negative_quantity_for_non_correction(self):
        with self.assertRaises(ValueError):
            BatchService.create_stock_adjustment(
                adjustment_type='spillage', quantity=Decimal('-5.00'),
                unit_cost=Decimal('10.00'), adjusted_by=self.user,
                product_batch=self.batch,
            )

    def test_rejects_quantity_exceeding_remaining_stock(self):
        with self.assertRaises(ValueError):
            BatchService.create_stock_adjustment(
                adjustment_type='spoilage', quantity=Decimal('150.00'),
                unit_cost=Decimal('10.00'), adjusted_by=self.user,
                product_batch=self.batch,
            )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('100.00'))  # unchanged

    def test_valid_spoilage_deducts_correctly_and_persists(self):
        BatchService.create_stock_adjustment(
            adjustment_type='spoilage', quantity=Decimal('20.00'),
            unit_cost=Decimal('10.00'), adjusted_by=self.user,
            product_batch=self.batch,
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('80.00'))

    def test_depleting_batch_sets_depleted_status(self):
        BatchService.create_stock_adjustment(
            adjustment_type='correction', quantity=Decimal('100.00'),
            unit_cost=Decimal('10.00'), adjusted_by=self.user,
            product_batch=self.batch,
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('0.00'))
        self.assertEqual(self.batch.status, 'depleted')

    def test_spoilage_or_expired_sets_disposed_not_depleted(self):
        BatchService.create_stock_adjustment(
            adjustment_type='spoilage', quantity=Decimal('100.00'),
            unit_cost=Decimal('10.00'), adjusted_by=self.user,
            product_batch=self.batch,
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, 'disposed')

    def test_requires_exactly_one_of_product_or_ingredient_batch(self):
        with self.assertRaises(ValueError):
            BatchService.create_stock_adjustment(
                adjustment_type='correction', quantity=Decimal('1.00'),
                unit_cost=Decimal('10.00'), adjusted_by=self.user,
            )  # neither provided

    # --- correction upper-bound cap (fix #11) ---

    def test_correction_cannot_push_remaining_above_initial(self):
        """A correction that would add more stock than was ever received must be rejected."""
        with self.assertRaises(ValueError):
            BatchService.create_stock_adjustment(
                adjustment_type='correction', quantity=Decimal('-50.00'),  # adds 50 back
                unit_cost=Decimal('10.00'), adjusted_by=self.user,
                product_batch=self.batch,  # remaining=100, initial=100 -> would become 150
            )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('100.00'))  # unchanged, rejected

    def test_correction_within_initial_quantity_succeeds(self):
        """Sanity check: a correction that adds back stock but stays within initial_quantity is fine."""
        self.batch.remaining_quantity = Decimal('60.00')
        self.batch.save()
        BatchService.create_stock_adjustment(
            adjustment_type='correction', quantity=Decimal('-30.00'),  # adds 30 back
            unit_cost=Decimal('10.00'), adjusted_by=self.user,
            product_batch=self.batch,  # 60 + 30 = 90, still <= initial 100
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('90.00'))

    def test_correction_restores_depleted_batch_to_available(self):
        self.batch.remaining_quantity = Decimal('0.00')
        self.batch.status = 'depleted'
        self.batch.save()

        BatchService.create_stock_adjustment(
            adjustment_type='correction', quantity=Decimal('-5.00'),
            unit_cost=Decimal('10.00'), adjusted_by=self.user,
            product_batch=self.batch,
        )

        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('5.00'))
        self.assertEqual(self.batch.status, 'available')

    def test_adjustment_api_rejects_nan_and_invalid_batch_id(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        payloads = [
            {
                'product_batch_id': self.batch.pk,
                'adjustment_type': 'correction',
                'quantity': 'NaN',
                'unit_cost': '10.00',
            },
            {
                'product_batch_id': {'invalid': True},
                'adjustment_type': 'correction',
                'quantity': '1.00',
                'unit_cost': '10.00',
            },
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = client.post(
                    '/inventory/stock-adjustments/', payload, format='json'
                )
                self.assertEqual(response.status_code, 400)

    def test_stock_count_api_rejects_nan_and_invalid_batch_id(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        payloads = [
            {'product_batch_id': self.batch.pk, 'counted_quantity': 'NaN'},
            {'product_batch_id': {'invalid': True}, 'counted_quantity': '1.00'},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = client.post(
                    '/inventory/stock-counts/', payload, format='json'
                )
                self.assertEqual(response.status_code, 400)


class ReconcileTests(TestCase):
    """Covers: reconcile() stale-read fix, null unit_price guard, IngBatchSerializer unit_price auto-fill gap."""

    def setUp(self):
        self.user = make_user()
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-002',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('100.00'),
            remaining_quantity=Decimal('100.00'),
            expiration_date=timezone.localdate() + timedelta(days=30),
        )

    def test_reconcile_with_shortage_creates_correction_adjustment(self):
        count = BatchService.reconcile(
            counted_quantity=Decimal('90.00'), counted_by=self.user,
            product_batch=self.batch,
        )
        self.assertEqual(count.expected_quantity, Decimal('100.00'))
        self.assertEqual(count.variance, Decimal('-10.00'))
        self.assertIsNotNone(count.resulting_adjustment)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('90.00'))

    def test_reconcile_with_no_variance_creates_no_adjustment(self):
        count = BatchService.reconcile(
            counted_quantity=Decimal('100.00'), counted_by=self.user,
            product_batch=self.batch,
        )
        self.assertEqual(count.variance, Decimal('0.00'))
        self.assertIsNone(count.resulting_adjustment)

    def test_reconcile_reads_current_db_state_not_stale_instance(self):
        """
        Simulates the staleness bug: caller holds an old in-memory batch object
        (remaining=100) while the real DB row has since changed (remaining=70,
        as if a concurrent sale happened). reconcile() must use the current DB
        value, not the stale one passed in.
        """
        stale_batch = ProductBatch.objects.get(pk=self.batch.pk)  # snapshot at remaining=100

        # Simulate a concurrent sale/adjustment changing the real row
        self.batch.remaining_quantity = Decimal('70.00')
        self.batch.save()

        count = BatchService.reconcile(
            counted_quantity=Decimal('65.00'), counted_by=self.user,
            product_batch=stale_batch,  # caller's stale object, remaining=100
        )
        # expected must reflect the CURRENT db value (70), not the stale one (100)
        self.assertEqual(count.expected_quantity, Decimal('70.00'))
        self.assertEqual(count.variance, Decimal('-5.00'))

    def test_reconcile_raises_on_null_unit_price_with_variance(self):
        self.batch.unit_price = None
        self.batch.save()
        with self.assertRaises(ValueError):
            BatchService.reconcile(
                counted_quantity=Decimal('90.00'), counted_by=self.user,
                product_batch=self.batch,
            )

    def test_reconcile_allows_null_unit_price_when_no_variance(self):
        """No variance means no adjustment is created, so a null unit_price shouldn't block it."""
        self.batch.unit_price = None
        self.batch.save()
        count = BatchService.reconcile(
            counted_quantity=Decimal('100.00'), counted_by=self.user,
            product_batch=self.batch,
        )
        self.assertEqual(count.variance, Decimal('0.00'))
        self.assertIsNone(count.resulting_adjustment)

    def test_ingredient_batch_auto_fills_unit_price_from_ingredient(self):
        """
        Covers the IngBatchSerializer.create() gap fix: an ingredient batch
        created without an explicit unit_price should inherit it from the
        ingredient, matching ProductBatch's existing behavior.
        """
        from inventory.serializers import IngBatchSerializer

        ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('25.00'),
            shelf_life=3, ingredient_type='raw_milk',
        )
        serializer = IngBatchSerializer(data={
            'ingredient_id': ingredient.pk,
            'quantity': '50.00',
            'expiration_date': (timezone.localdate() + timedelta(days=30)).isoformat(),
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        batch = serializer.save()
        self.assertEqual(batch.unit_price, Decimal('25.00')) # type: ignore


class BatchQuantityUpdateTests(TestCase):
    """Covers: fix #7 — PATCH quantity used to silently no-op."""

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-003',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('100.00'),
            remaining_quantity=Decimal('100.00'),
            expiration_date=timezone.localdate() + timedelta(days=30),
        )

    def test_patching_quantity_does_not_error_and_does_not_touch_remaining_quantity(self):
        """
        quantity is intentionally write_only/create-only. This test locks in
        that PATCHing it is silently ignored rather than raising OR actually
        changing stock outside the audited adjustment/reconcile paths —
        this is the corrected, intentional behavior, not the old bug.
        """
        from inventory.serializers import ProdBatchSerializer

        serializer = ProdBatchSerializer(
            self.batch, data={'quantity': '9999.00', 'notes': 'test note'}, partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('100.00'))  # untouched
        self.assertEqual(self.batch.notes, 'test note')  # other fields still patch fine

    def test_patching_expiration_date_persists(self):
        from inventory.serializers import ProdBatchSerializer

        new_date = timezone.localdate() + timedelta(days=60)
        serializer = ProdBatchSerializer(
            self.batch, data={'expiration_date': new_date.isoformat()}, partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.expiration_date, new_date)


class DeductBatchTests(TestCase):
    """
    Sanity coverage for FEFO deduction logic. Dates are computed relative to
    'today' (not hardcoded) since Round 7 made deduction expiration-aware —
    a hardcoded date drifts into the past over time and would silently start
    excluding these fixture batches from FEFO instead of actually testing
    the earliest-expiring-first behavior.
    """

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            shelf_life=7,
        )
        today = timezone.localdate()
        # Two batches, different expiration dates — FEFO should drain the soonest first.
        self.batch_soon = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-004',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('30.00'), remaining_quantity=Decimal('30.00'),
            expiration_date=today + timedelta(days=5),
        )
        self.batch_later = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-TEST-005',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('50.00'), remaining_quantity=Decimal('50.00'),
            expiration_date=today + timedelta(days=35),
        )

    def test_deducts_from_earliest_expiring_batch_first(self):
        consumed = BatchService.deduct_product_batch(self.product, Decimal('20.00'))
        self.assertEqual(len(consumed), 1)
        self.batch_soon.refresh_from_db()
        self.batch_later.refresh_from_db()
        self.assertEqual(self.batch_soon.remaining_quantity, Decimal('10.00'))
        self.assertEqual(self.batch_later.remaining_quantity, Decimal('50.00'))  # untouched

    def test_deducts_across_multiple_batches_when_first_is_insufficient(self):
        consumed = BatchService.deduct_product_batch(self.product, Decimal('40.00'))
        self.assertEqual(len(consumed), 2)
        self.batch_soon.refresh_from_db()
        self.batch_later.refresh_from_db()
        self.assertEqual(self.batch_soon.remaining_quantity, Decimal('0.00'))
        self.assertEqual(self.batch_soon.status, 'depleted')
        self.assertEqual(self.batch_later.remaining_quantity, Decimal('40.00'))

    def test_raises_on_insufficient_total_stock(self):
        with self.assertRaises(ValueError):
            BatchService.deduct_product_batch(self.product, Decimal('1000.00'))

    def test_raises_on_zero_or_negative_quantity(self):
        with self.assertRaises(ValueError):
            BatchService.deduct_product_batch(self.product, Decimal('0.00'))


class BatchQuantityPriceValidationTests(TestCase):
    """
    Covers: ProdBatchSerializer/IngBatchSerializer used to accept quantity <= 0
    and negative unit_price with zero validation. Both now reject via
    min_value on quantity, and unit_price is bounded at >= 0.00 — for
    ProductBatch via an explicit serializer field override, and for
    IngredientBatch via the model-level MinValueValidator that DRF's
    ModelSerializer auto-copies onto the generated field (IngBatchSerializer
    never overrode unit_price, so this is the only thing now guarding it).
    """

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            shelf_life=7,
        )
        self.ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('25.00'),
            shelf_life=3, ingredient_type='raw_milk',
        )
        self.future_date = (timezone.localdate() + timedelta(days=30)).isoformat()

    # --- ProductBatch: quantity ---

    def test_product_batch_rejects_zero_quantity(self):
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '0.00',
            'expiration_date': self.future_date,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('quantity', serializer.errors)

    def test_product_batch_rejects_negative_quantity(self):
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '-50.00',
            'expiration_date': self.future_date,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('quantity', serializer.errors)

    def test_product_batch_accepts_minimum_valid_quantity(self):
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '0.01',
            'expiration_date': self.future_date,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    # --- ProductBatch: unit_price ---

    def test_product_batch_rejects_negative_unit_price(self):
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'unit_price': '-5.00', 'expiration_date': self.future_date,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('unit_price', serializer.errors)

    def test_product_batch_accepts_zero_unit_price(self):
        """Zero is a boundary-valid price (e.g. a free/promotional batch), not rejected."""
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'unit_price': '0.00', 'expiration_date': self.future_date,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_product_batch_has_no_default_selling_price(self):
        """A batch cost is optional and never inherited from a product selling price."""
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'expiration_date': self.future_date,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        batch = serializer.save()
        self.assertIsNone(cast(ProductBatch, batch).unit_price)

    # --- IngredientBatch: quantity ---

    def test_ingredient_batch_rejects_zero_quantity(self):
        from inventory.serializers import IngBatchSerializer
        serializer = IngBatchSerializer(data={
            'ingredient_id': self.ingredient.pk, 'quantity': '0.00',
            'expiration_date': self.future_date,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('quantity', serializer.errors)

    def test_ingredient_batch_rejects_negative_quantity(self):
        from inventory.serializers import IngBatchSerializer
        serializer = IngBatchSerializer(data={
            'ingredient_id': self.ingredient.pk, 'quantity': '-10.00',
            'expiration_date': self.future_date,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('quantity', serializer.errors)

    # --- IngredientBatch: unit_price (model-level validator, no serializer override) ---

    def test_ingredient_batch_rejects_negative_unit_price(self):
        from inventory.serializers import IngBatchSerializer
        serializer = IngBatchSerializer(data={
            'ingredient_id': self.ingredient.pk, 'quantity': '10.00',
            'unit_price': '-1.00', 'expiration_date': self.future_date,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('unit_price', serializer.errors)

    def test_ingredient_batch_accepts_zero_unit_price(self):
        from inventory.serializers import IngBatchSerializer
        serializer = IngBatchSerializer(data={
            'ingredient_id': self.ingredient.pk, 'quantity': '10.00',
            'unit_price': '0.00', 'expiration_date': self.future_date,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)


class ProductIngredientPriceValidationTests(TestCase):
    """Catalog products reject selling prices; ingredient costs stay validated."""

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')

    def test_product_serializer_rejects_negative_unit_price(self):
        from inventory.serializers import ProductSerializer
        serializer = ProductSerializer(data={
            'name': 'Bad Milk', 'unit': 'liter', 'unit_price': '-1.00',
            'shelf_life': 7, 'category_id': self.category.pk,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('unit_price', serializer.errors)

    def test_ingredient_serializer_rejects_negative_unit_price(self):
        from inventory.serializers import IngredientSerializer
        serializer = IngredientSerializer(data={
            'name': 'Bad Ingredient', 'unit': 'liter', 'unit_price': '-1.00',
            'shelf_life': 3,
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('unit_price', serializer.errors)

    def test_product_model_has_no_catalog_selling_price(self):
        product = Product(category=self.category, name='Milk', unit='liter', shelf_life=7)
        product.full_clean()
        self.assertNotIn('unit_price', {field.name for field in Product._meta.fields})

    def test_ingredient_model_full_clean_rejects_negative_unit_price(self):
        ingredient = Ingredient(
            name='Bad Ingredient', unit='liter',
            unit_price=Decimal('-1.00'), shelf_life=3,
        )
        with self.assertRaises(Exception):
            ingredient.full_clean()


class BatchSequenceServiceTests(TestCase):
    """
    Covers next_sequence(): replaces the old count()-based numbering (racy —
    select_for_update() can't lock a row that doesn't exist yet) with a
    dedicated counter row that IS locked and incremented atomically.
    """

    def test_first_call_for_a_prefix_returns_one(self):
        seq = next_sequence('PRD')
        self.assertEqual(seq, 1)

    def test_sequential_calls_increment(self):
        first = next_sequence('PRD')
        second = next_sequence('PRD')
        third = next_sequence('PRD')
        self.assertEqual([first, second, third], [1, 2, 3])

    def test_different_prefixes_are_independent(self):
        prd_seq = next_sequence('PRD')
        ing_seq = next_sequence('ING')
        self.assertEqual(prd_seq, 1)
        self.assertEqual(ing_seq, 1)  # ING's counter didn't inherit PRD's progress

    def test_sequence_row_created_per_prefix_year_month(self):
        next_sequence('PRD')
        self.assertEqual(BatchSequence.objects.filter(prefix='PRD').count(), 1)
        row = BatchSequence.objects.get(prefix='PRD')
        self.assertEqual(row.last_seq, 1)

    def test_repeated_calls_reuse_same_row_not_create_new_ones(self):
        for _ in range(5):
            next_sequence('PRD')
        self.assertEqual(BatchSequence.objects.filter(prefix='PRD').count(), 1)
        self.assertEqual(BatchSequence.objects.get(prefix='PRD').last_seq, 5)

    def test_serializer_create_uses_sequence_and_produces_expected_format(self):
        """End-to-end: ProdBatchSerializer.create() calls next_sequence() and
        the resulting batch_number matches PRD-YYMM-NNN with the right sequence."""
        from inventory.serializers import ProdBatchSerializer

        category = Category.objects.create(name='Dairy')
        product = Product.objects.create(
            category=category, name='Fresh Milk', unit='liter',
            shelf_life=7,
        )
        future_date = (timezone.localdate() + timedelta(days=30)).isoformat()

        serializer1 = ProdBatchSerializer(data={
            'product_id': product.pk, 'quantity': '10.00', 'expiration_date': future_date,
        })
        self.assertTrue(serializer1.is_valid(), serializer1.errors)
        batch1 = serializer1.save()

        serializer2 = ProdBatchSerializer(data={
            'product_id': product.pk, 'quantity': '10.00', 'expiration_date': future_date,
        })
        self.assertTrue(serializer2.is_valid(), serializer2.errors)
        batch2 = serializer2.save()

        now = timezone.now()
        expected_prefix = f"PRD-{now.strftime('%y')}{now.strftime('%m')}-"
        self.assertEqual(batch1.batch_number, f"{expected_prefix}001") # type: ignore
        self.assertEqual(batch2.batch_number, f"{expected_prefix}002") # type: ignore

    def test_ingredient_batch_sequence_is_independent_of_product_batch_sequence(self):
        from inventory.serializers import ProdBatchSerializer, IngBatchSerializer

        category = Category.objects.create(name='Dairy')
        product = Product.objects.create(
            category=category, name='Fresh Milk', unit='liter',
            shelf_life=7,
        )
        ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('25.00'),
            shelf_life=3, ingredient_type='raw_milk',
        )
        future_date = (timezone.localdate() + timedelta(days=30)).isoformat()

        prod_serializer = ProdBatchSerializer(data={
            'product_id': product.pk, 'quantity': '10.00', 'expiration_date': future_date,
        })
        self.assertTrue(prod_serializer.is_valid(), prod_serializer.errors)
        prod_batch = prod_serializer.save()

        ing_serializer = IngBatchSerializer(data={
            'ingredient_id': ingredient.pk, 'quantity': '10.00', 'expiration_date': future_date,
        })
        self.assertTrue(ing_serializer.is_valid(), ing_serializer.errors)
        ing_batch = ing_serializer.save()

        self.assertIn('PRD-', prod_batch.batch_number) # type: ignore
        self.assertIn('ING-', ing_batch.batch_number) # type: ignore
        self.assertTrue(prod_batch.batch_number.endswith('001')) # type: ignore
        self.assertTrue(ing_batch.batch_number.endswith('001')) # type: ignore


class BatchSequenceConcurrencyTests(TransactionTestCase):
    """
    The actual race the BatchSequence redesign exists to close. Unlike
    TestCase, TransactionTestCase doesn't wrap the test in one shared
    connection/transaction, so separate threads here get separate real
    Postgres connections and can genuinely contend for the row lock inside
    next_sequence(). Ten threads call next_sequence('PRD') concurrently;
    a correct implementation hands out exactly 1..10 with no duplicates and
    no gaps. The old count()-based approach would be expected to produce
    duplicates under this same test.
    """

    def test_concurrent_calls_never_produce_duplicate_sequence_numbers(self):
        results = []
        errors = []
        lock = threading.Lock()

        def worker():
            try:
                seq = next_sequence('PRD')
                with lock:
                    results.append(seq)
            except Exception as e:
                with lock:
                    errors.append(e)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"next_sequence() raised under concurrency: {errors}")
        self.assertEqual(len(results), 10)
        self.assertEqual(sorted(results), list(range(1, 11)))  # no dupes, no gaps


class BatchNumberUniquenessTests(TestCase):
    """
    Covers the unique=True constraint added to ProductBatch.batch_number and
    IngredientBatch.batch_number. BatchSequence makes collisions practically
    impossible now, but this is the hard DB-level backstop for any write
    path that bypasses the sequence entirely (bulk import, manual fixture,
    a future bug).
    """

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            shelf_life=7,
        )
        self.ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('25.00'),
            shelf_life=3, ingredient_type='raw_milk',
        )
        self.future_date = timezone.localdate() + timedelta(days=30)

    def test_duplicate_product_batch_number_rejected_at_db_level(self):
        ProductBatch.objects.create(
            product=self.product, batch_number='PRD-DUP-001',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=self.future_date,
        )
        with self.assertRaises(IntegrityError):
            ProductBatch.objects.create(
                product=self.product, batch_number='PRD-DUP-001',  # same number
                unit_price=Decimal('50.00'),
                initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
                expiration_date=self.future_date,
            )

    def test_duplicate_ingredient_batch_number_rejected_at_db_level(self):
        IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='ING-DUP-001',
            unit_price=Decimal('25.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=self.future_date,
        )
        with self.assertRaises(IntegrityError):
            IngredientBatch.objects.create(
                ingredient=self.ingredient, batch_number='ING-DUP-001',  # same number
                unit_price=Decimal('25.00'),
                initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
                expiration_date=self.future_date,
            )

    def test_product_and_ingredient_batches_can_share_a_prefix_free_number_space(self):
        """Uniqueness is per-model (separate DB tables), so a PRD-prefixed and
        an ING-prefixed number never collide regardless of content — this
        just confirms the constraint doesn't accidentally span both tables."""
        ProductBatch.objects.create(
            product=self.product, batch_number='SHARED-001',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=self.future_date,
        )
        # Same literal string, different model/table — must NOT raise.
        IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='SHARED-001',
            unit_price=Decimal('25.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=self.future_date,
        )
        self.assertEqual(ProductBatch.objects.filter(batch_number='SHARED-001').count(), 1)
        self.assertEqual(IngredientBatch.objects.filter(batch_number='SHARED-001').count(), 1)


class ExpiredBatchExclusionTests(TestCase):
    """
    Covers: deduct_product_batch()/deduct_ingredient_batch() previously
    filtered only on status='available', never on expiration_date. Nothing
    in this app automatically flips status to 'expired' when a batch's date
    passes, so an expired-but-still-'available' batch was silently sellable
    at checkout forever, until a human manually filed a write-off adjustment.
    """

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            shelf_life=7,
        )
        self.ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('25.00'),
            shelf_life=3, ingredient_type='raw_milk',
        )
        self.today = timezone.localdate()

    # --- ProductBatch ---

    def test_expired_product_batch_excluded_from_deduction(self):
        expired = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-EXP-001',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('50.00'), remaining_quantity=Decimal('50.00'),
            expiration_date=self.today - timedelta(days=1),  # expired yesterday
        )
        valid = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-EXP-002',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('50.00'), remaining_quantity=Decimal('50.00'),
            expiration_date=self.today + timedelta(days=10),
        )

        consumed = BatchService.deduct_product_batch(self.product, Decimal('20.00'))

        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0][0].pk, valid.pk)
        expired.refresh_from_db()
        valid.refresh_from_db()
        self.assertEqual(expired.remaining_quantity, Decimal('50.00'))  # untouched
        self.assertEqual(valid.remaining_quantity, Decimal('30.00'))

    def test_deduction_fails_when_only_expired_stock_exists(self):
        ProductBatch.objects.create(
            product=self.product, batch_number='PRD-EXP-003',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('100.00'), remaining_quantity=Decimal('100.00'),
            expiration_date=self.today - timedelta(days=5),
        )
        with self.assertRaises(ValueError):
            BatchService.deduct_product_batch(self.product, Decimal('10.00'))

    def test_fefo_uses_active_local_date_for_expiry_boundary(self):
        local_today = date(2026, 9, 8)
        ProductBatch.objects.create(
            product=self.product, batch_number='PRD-LOCAL-DATE',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=local_today - timedelta(days=1),
        )
        with patch(
            'inventory.services.fefo_service.timezone.localdate',
            return_value=local_today,
        ):
            with self.assertRaises(ValueError):
                BatchService.deduct_product_batch(self.product, Decimal('1.00'))

    def test_batch_expiring_today_is_still_sellable(self):
        """Boundary case: a batch dated exactly today hasn't expired yet — still fair game."""
        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-EXP-004',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('20.00'), remaining_quantity=Decimal('20.00'),
            expiration_date=self.today,
        )
        consumed = BatchService.deduct_product_batch(self.product, Decimal('5.00'))
        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0][0].pk, batch.pk)

    # --- IngredientBatch ---

    def test_expired_ingredient_batch_excluded_from_deduction(self):
        expired = IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='ING-EXP-001',
            unit_price=Decimal('25.00'),
            initial_quantity=Decimal('50.00'), remaining_quantity=Decimal('50.00'),
            expiration_date=self.today - timedelta(days=1),
        )
        valid = IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='ING-EXP-002',
            unit_price=Decimal('25.00'),
            initial_quantity=Decimal('50.00'), remaining_quantity=Decimal('50.00'),
            expiration_date=self.today + timedelta(days=10),
        )

        consumed = BatchService.deduct_ingredient_batch(self.ingredient, Decimal('20.00'))

        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0][0].pk, valid.pk)
        expired.refresh_from_db()
        valid.refresh_from_db()
        self.assertEqual(expired.remaining_quantity, Decimal('50.00'))  # untouched
        self.assertEqual(valid.remaining_quantity, Decimal('30.00'))

    def test_ingredient_deduction_fails_when_only_expired_stock_exists(self):
        IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='ING-EXP-003',
            unit_price=Decimal('25.00'),
            initial_quantity=Decimal('100.00'), remaining_quantity=Decimal('100.00'),
            expiration_date=self.today - timedelta(days=5),
        )
        with self.assertRaises(ValueError):
            BatchService.deduct_ingredient_batch(self.ingredient, Decimal('10.00'))


class BatchStatusReadOnlyTests(TestCase):
    """
    Covers: status was directly PATCHable on both batch serializers, bypassing
    every audited transition. A valid choice like {"status": "available"}
    could silently un-dispose a batch that was written off for spoilage,
    re-entering it into the sellable FEFO pool with no adjustment record.
    Same treatment as quantity from the prior round: read-only, only
    mutable via create_stock_adjustment()/reconcile().
    """

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            shelf_life=7,
        )
        self.ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('25.00'),
            shelf_life=3, ingredient_type='raw_milk',
        )
        self.future_date = timezone.localdate() + timedelta(days=30)

    def test_product_batch_status_ignored_on_patch(self):
        from inventory.serializers import ProdBatchSerializer

        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-STATUS-001',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=self.future_date, status='disposed',
        )
        serializer = ProdBatchSerializer(batch, data={'status': 'available'}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'disposed')  # unchanged — patch silently ignored

    def test_ingredient_batch_status_ignored_on_patch(self):
        from inventory.serializers import IngBatchSerializer

        batch = IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='ING-STATUS-001',
            unit_price=Decimal('25.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=self.future_date, status='depleted',
        )
        serializer = IngBatchSerializer(batch, data={'status': 'available'}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'depleted')  # unchanged — patch silently ignored

    def test_new_product_batch_defaults_to_available_regardless_of_input(self):
        from inventory.serializers import ProdBatchSerializer

        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'expiration_date': self.future_date.isoformat(),
            'status': 'disposed',  # attempted spoof on create
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        batch = serializer.save()
        self.assertEqual(batch.status, 'available') # type: ignore

    def test_status_still_mutable_via_create_stock_adjustment(self):
        """Sanity check: read-only in the serializer, but the audited path still works."""
        user = make_user()
        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-STATUS-002',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=self.future_date,
        )
        BatchService.create_stock_adjustment(
            adjustment_type='spoilage', quantity=Decimal('10.00'),
            unit_cost=Decimal('10.00'), adjusted_by=user,
            product_batch=batch,
        )
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'disposed')


class BatchDateValidationTests(TestCase):
    """
    Covers: expiration_date wasn't checked against date_received on batch
    creation or update, so a batch could be logged as already expired the
    moment it was received.
    """

    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            category=self.category, name='Fresh Milk', unit='liter',
            shelf_life=7,
        )
        self.ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('25.00'),
            shelf_life=3, ingredient_type='raw_milk',
        )
        self.today = timezone.localdate()

    # --- ProductBatch: create ---

    def test_product_batch_rejects_expiration_before_explicit_date_received(self):
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'date_received': self.today.isoformat(),
            'expiration_date': (self.today - timedelta(days=1)).isoformat(),
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('expiration_date', serializer.errors)

    def test_product_batch_rejects_expiration_before_default_date_received(self):
        """date_received omitted -> defaults to today; expiration in the past must still be rejected."""
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'expiration_date': (self.today - timedelta(days=1)).isoformat(),
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('expiration_date', serializer.errors)

    def test_product_batch_allows_expiration_equal_to_date_received(self):
        """Same-day expiration is a boundary-valid case, not rejected."""
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'date_received': self.today.isoformat(),
            'expiration_date': self.today.isoformat(),
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_product_batch_allows_future_expiration(self):
        from inventory.serializers import ProdBatchSerializer
        serializer = ProdBatchSerializer(data={
            'product_id': self.product.pk, 'quantity': '10.00',
            'date_received': self.today.isoformat(),
            'expiration_date': (self.today + timedelta(days=30)).isoformat(),
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

    # --- ProductBatch: update ---

    def test_product_batch_update_rejects_new_expiration_before_existing_date_received(self):
        from inventory.serializers import ProdBatchSerializer
        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-DATE-001',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            date_received=self.today, expiration_date=self.today + timedelta(days=30),
        )
        serializer = ProdBatchSerializer(
            batch, data={'expiration_date': (self.today - timedelta(days=1)).isoformat()}, partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('expiration_date', serializer.errors)

    def test_product_batch_update_rejects_new_date_received_after_existing_expiration(self):
        from inventory.serializers import ProdBatchSerializer
        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-DATE-002',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            date_received=self.today, expiration_date=self.today + timedelta(days=5),
        )
        serializer = ProdBatchSerializer(
            batch, data={'date_received': (self.today + timedelta(days=10)).isoformat()}, partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('expiration_date', serializer.errors)

    def test_product_batch_update_unrelated_field_still_succeeds(self):
        """Regression guard: the new validate() shouldn't block ordinary patches
        that don't touch either date field."""
        from inventory.serializers import ProdBatchSerializer
        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-DATE-003',
            unit_price=Decimal('50.00'),
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            date_received=self.today, expiration_date=self.today + timedelta(days=30),
        )
        serializer = ProdBatchSerializer(batch, data={'notes': 'restocked shelf'}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    # --- IngredientBatch: create ---

    def test_ingredient_batch_rejects_expiration_before_date_received(self):
        from inventory.serializers import IngBatchSerializer
        serializer = IngBatchSerializer(data={
            'ingredient_id': self.ingredient.pk, 'quantity': '10.00',
            'date_received': self.today.isoformat(),
            'expiration_date': (self.today - timedelta(days=1)).isoformat(),
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('expiration_date', serializer.errors)

    def test_ingredient_batch_allows_future_expiration(self):
        from inventory.serializers import IngBatchSerializer
        serializer = IngBatchSerializer(data={
            'ingredient_id': self.ingredient.pk, 'quantity': '10.00',
            'date_received': self.today.isoformat(),
            'expiration_date': (self.today + timedelta(days=10)).isoformat(),
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)


class InventoryAuditRegressionTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.user = make_user('inventoryauditadmin', role='admin')
        self.category = Category.objects.create(name='Audit Category')
        self.product = Product.objects.create(
            category=self.category, name='Audit Product', unit='piece',
            shelf_life=7,
        )
        self.ingredient = Ingredient.objects.create(
            name='Audit Ingredient', unit='kg', unit_price=Decimal('5.00'),
            shelf_life=7,
        )

    def test_expired_product_stock_is_excluded_from_total_and_low_stock_check(self):
        from inventory.serializers import ProductSerializer

        ProductBatch.objects.create(
            product=self.product, batch_number='PRD-AUDIT-EXP',
            initial_quantity=Decimal('100.00'), remaining_quantity=Decimal('100.00'),
            expiration_date=self.today - timedelta(days=1), status='available',
        )

        self.assertEqual(cast(dict[str, object], ProductSerializer(self.product).data)['total_stock'], 0)
        alert = next(
            item for item in BatchService.check_product_stock()
            if item['product'].pk == self.product.pk
        )
        self.assertEqual(alert['remaining_quantity'], Decimal('0.00'))

    def test_expired_ingredient_stock_is_excluded_from_total_and_low_stock_check(self):
        from inventory.serializers import IngredientSerializer

        IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='ING-AUDIT-EXP',
            initial_quantity=Decimal('100.00'), remaining_quantity=Decimal('100.00'),
            expiration_date=self.today - timedelta(days=1), status='available',
        )

        self.assertEqual(cast(dict[str, object], IngredientSerializer(self.ingredient).data)['total_stock'], 0)
        alert = next(
            item for item in BatchService.check_ingredient_stock()
            if item['ingredient'].pk == self.ingredient.pk
        )
        self.assertEqual(alert['remaining_quantity'], Decimal('0.00'))

    def test_product_batch_cannot_be_reassigned(self):
        from inventory.serializers import ProdBatchSerializer

        other = Product.objects.create(
            category=self.category, name='Other Product', unit='piece',
            shelf_life=7,
        )
        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-AUDIT-MOVE',
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('7.00'),
            expiration_date=self.today + timedelta(days=7),
        )
        serializer = ProdBatchSerializer(
            batch, data={'product_id': other.pk}, partial=True,
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('product_id', serializer.errors)

    def test_ingredient_batch_cannot_be_reassigned(self):
        from inventory.serializers import IngBatchSerializer

        other = Ingredient.objects.create(
            name='Other Ingredient', unit='kg', unit_price=Decimal('5.00'),
            shelf_life=7,
        )
        batch = IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='ING-AUDIT-MOVE',
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('7.00'),
            expiration_date=self.today + timedelta(days=7),
        )
        serializer = IngBatchSerializer(
            batch, data={'ingredient_id': other.pk}, partial=True,
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn('ingredient_id', serializer.errors)

    def test_negative_shelf_life_is_rejected(self):
        from inventory.serializers import IngredientSerializer, ProductSerializer

        product = ProductSerializer(data={
            'category_id': self.category.pk, 'name': 'Invalid Product',
            'unit': 'piece', 'unit_price': '1.00', 'shelf_life': -1,
        })
        ingredient = IngredientSerializer(data={
            'name': 'Invalid Ingredient', 'unit': 'kg', 'unit_price': '1.00',
            'shelf_life': -1,
        })

        self.assertFalse(product.is_valid())
        self.assertIn('shelf_life', product.errors)
        self.assertFalse(ingredient.is_valid())
        self.assertIn('shelf_life', ingredient.errors)

    def test_ingredient_batch_grade_is_available_through_serializer(self):
        from inventory.serializers import IngBatchSerializer

        serializer = IngBatchSerializer(data={
            'ingredient_id': self.ingredient.pk, 'quantity': '10.00',
            'grade': 'A',
            'expiration_date': (self.today + timedelta(days=7)).isoformat(),
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        batch = serializer.save()

        self.assertEqual(cast(IngredientBatch, batch).grade, 'A')
        self.assertEqual(cast(dict[str, object], IngBatchSerializer(batch).data)['grade'], 'A')

    def test_terminal_batch_rejects_correction_without_creating_phantom_stock(self):
        batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-AUDIT-DISPOSED',
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('0.00'),
            expiration_date=self.today + timedelta(days=7), status='disposed',
        )

        with self.assertRaises(ValueError):
            BatchService.create_stock_adjustment(
                adjustment_type='correction', quantity=Decimal('-5.00'),
                unit_cost=Decimal('10.00'), adjusted_by=self.user,
                product_batch=batch,
            )

        batch.refresh_from_db()
        self.assertEqual(batch.remaining_quantity, Decimal('0.00'))
        self.assertEqual(batch.status, 'disposed')
        self.assertFalse(StockAdjustment.objects.filter(product_batch=batch).exists())

    def test_adjustment_requires_exactly_one_batch_at_database_level(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockAdjustment.objects.create(
                adjustment_type='correction', quantity=Decimal('1.00'),
                unit_cost=Decimal('1.00'), adjusted_by=self.user,
            )

    def test_stock_count_requires_exactly_one_batch_at_database_level(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockCount.objects.create(
                expected_quantity=Decimal('1.00'), counted_quantity=Decimal('1.00'),
                variance=Decimal('0.00'), counted_by=self.user,
            )


class FEFOTransactionBoundaryTests(TransactionTestCase):
    def setUp(self):
        category = Category.objects.create(name='Transaction Boundary')
        self.product = Product.objects.create(
            category=category, name='Transaction Product', unit='piece',
            shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-AUDIT-ATOMIC',
            initial_quantity=Decimal('10.00'), remaining_quantity=Decimal('10.00'),
            expiration_date=timezone.localdate() + timedelta(days=7),
        )

    def test_direct_fefo_call_opens_its_own_transaction(self):
        consumed = BatchService.deduct_product_batch(self.product, Decimal('2.00'))

        self.assertEqual(consumed[0][1], Decimal('2.00'))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('8.00'))


class CategoryIconAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_user('iconadmin', role='admin')
        self.staff = make_user('iconstaff')
        self.client.force_authenticate(user=self.admin)

    def test_admin_can_set_edit_and_clear_icon(self):
        created = self.client.post('/inventory/categories/', {
            'name': 'Soft Cheese', 'icon': 'cheese',
        }, format='json')
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data['icon'], 'cheese')
        category_id = created.data['id']

        updated = self.client.patch(f'/inventory/categories/{category_id}/', {
            'icon': 'milk',
        }, format='json')
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data['icon'], 'milk')
        self.assertEqual(Category.objects.get(pk=category_id).icon, 'milk')

        cleared = self.client.patch(f'/inventory/categories/{category_id}/', {
            'icon': '',
        }, format='json')
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.data['icon'], '')

    def test_invalid_icon_is_rejected_and_previous_icon_is_preserved(self):
        category = Category.objects.create(name='Butter', icon='butter')
        response = self.client.patch(f'/inventory/categories/{category.pk}/', {
            'icon': 'not-an-icon',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('icon', response.data)
        category.refresh_from_db()
        self.assertEqual(category.icon, 'butter')

    def test_staff_can_read_icons_but_cannot_change_them(self):
        category = Category.objects.create(name='Yogurt', icon='yogurt')
        self.client.force_authenticate(user=self.staff)
        listed = self.client.get('/inventory/categories/')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data[0]['icon'], 'yogurt')
        denied = self.client.patch(f'/inventory/categories/{category.pk}/', {
            'icon': 'cream',
        }, format='json')
        self.assertEqual(denied.status_code, 403)

    def test_icon_options_match_the_values_accepted_by_the_api(self):
        response = self.client.get('/inventory/categories/icon-options/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['value'] for item in response.data],
                         ['', *[value for value, _ in Category.ICON_CHOICES]])


# Catalog query performance
# ----------------------------------------------------------------------------

class CatalogQueryTests(TestCase):
    def setUp(self):
        FEFOConf.get_config()

    def test_ingredient_catalog_and_nested_batches_use_bounded_queries(self):
        for i in range(12):
            ingredient = Ingredient.objects.create(
                name=f'Ingredient {i}', unit='liter', unit_price=20, shelf_life=7,
            )
            for offset, quantity in [(2, 10), (-1, 5)]:
                IngredientBatch.objects.create(
                    ingredient=ingredient, batch_number=f'ING-{i}-{offset}',
                    initial_quantity=quantity, remaining_quantity=quantity,
                    expiration_date=timezone.localdate() + timedelta(days=offset),
                )
        for view_type, serializer, nested, queries in [
            (IngredientViewSet, IngredientSerializer, False, 1),
            (IngredientBatchViewSet, IngBatchSerializer, True, 2),
        ]:
            view = view_type()
            view.action = 'list'
            view.request = SimpleNamespace(query_params={})
            with self.assertNumQueries(queries + 1):
                data = serializer(view.get_queryset(), many=True).data
            self.assertEqual(len(data), 24 if nested else 12)
            self.assertTrue(all(
                Decimal(str((row['ingredient'] if nested else row)['total_stock'])) == 10
                for row in data
            ))

    def test_catalog_and_nested_batches_use_bounded_queries_and_exclude_expired_stock(self):
        category=Category.objects.create(name='Milk')
        for i in range(12):
            product=Product.objects.create(name=f'Milk {i}',category=category,unit='liter',shelf_life=7)
            for offset,qty in [(2,10),(-1,5)]:
                ProductBatch.objects.create(product=product,batch_number=f'{i}-{offset}',
                    initial_quantity=qty,remaining_quantity=qty,status='available',
                    expiration_date=timezone.localdate()+timedelta(days=offset))
        for view_type,serializer,nested in [(ProductViewSet,ProductSerializer,False),(ProductBatchViewSet,ProdBatchSerializer,True)]:
            view=view_type();view.action='list'
            view.request=SimpleNamespace(user=SimpleNamespace(role='admin'),query_params={})
            with self.assertNumQueries(3):
                data=serializer(view.get_queryset(),many=True).data
            self.assertEqual(len(data),24 if nested else 12)
            self.assertTrue(all(Decimal(str((r['product'] if nested else r)['total_stock']))==10 for r in data))

        from inventory.services.stock_check_service import check_product_expiration
        FEFOConf.get_config()
        with self.assertNumQueries(4):
            data=ProdBatchSerializer(check_product_expiration(),many=True).data
        self.assertEqual(len(data),12)
        self.assertTrue(all(Decimal(str(r['product']['total_stock']))==10 for r in data))


# Spoilage, complimentary items and free taste
# ----------------------------------------------------------------------------

class InventoryOutflowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user('outflowstaff')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.category = Category.objects.create(name='Outflow Dairy')
        self.product = Product.objects.create(category=self.category, name='Milk', unit='liter', shelf_life=7)
        self.batch = ProductBatch.objects.create(product=self.product, batch_number='OUTFLOW-STOCK',
            initial_quantity=10, remaining_quantity=10, unit_price=25,
            expiration_date=timezone.localdate() + timedelta(days=7))
        self.url = '/inventory/stock-adjustments/'

    def record(self, kind, **extra):
        return self.client.post(self.url, {'product_batch_id': self.batch.pk,
            'adjustment_type': kind, 'quantity': '2.00', 'unit_cost': '25.00',
            'reason': 'Inventory usage record', **extra}, format='json')

    def test_spoilage_complimentary_and_free_taste_are_distinct_inventory_records(self):
        signature = load_snapshot().revision
        version = data_version()
        for kind in ('spoilage', 'complimentary', 'free_taste'):
            with self.capture_commit_callbacks(execute=True):
                response = self.record(kind)
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(response.data['adjustment_type'], kind)
            self.assertEqual(response.data['quantity'], '2.00')
            self.assertEqual(response.data['unit_cost'], '25.00')
            self.assertEqual(response.data['adjusted_by']['id'], self.user.pk)
            self.assertEqual(response.data['reason'], 'Inventory usage record')
            self.assertTrue(response.data['created_at'])
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('4'))
        self.assertEqual(Decimal(str(cast(dict[str, object], ProductSerializer(self.product).data)['total_stock'])), Decimal('4'))
        self.assertNotEqual(data_version(), version)
        self.assertEqual(StockAdjustment.objects.count(), 3)
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Order.objects.exists())
        self.assertEqual(load_snapshot().revision, signature)

    def test_complimentary_and_free_taste_deplete_stock_and_spoilage_disposes_it(self):
        for kind, expected in (('complimentary', 'depleted'), ('free_taste', 'depleted'), ('spoilage', 'disposed')):
            self.batch.remaining_quantity = Decimal('10')
            self.batch.status = 'available'
            self.batch.save()
            response = self.record(kind, quantity='10')
            self.assertEqual(response.status_code, 201, response.data)
            self.batch.refresh_from_db()
            self.assertEqual(self.batch.remaining_quantity, 0)
            self.assertEqual(self.batch.status, expected)

    def test_ingredient_batches_use_the_same_outflow_records(self):
        ingredient = Ingredient.objects.create(name='Raw Milk', unit='L', unit_price=20, shelf_life=7)
        batch = IngredientBatch.objects.create(ingredient=ingredient, batch_number='OUTFLOW-INGREDIENT',
            initial_quantity=10, remaining_quantity=10, unit_price=20,
            expiration_date=timezone.localdate() + timedelta(days=7))
        response = self.record('free_taste', product_batch_id=None, ingredient_batch_id=batch.pk, unit_cost='20')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data['product_batch'])
        self.assertEqual(response.data['ingredient_batch']['id'], batch.pk)
        batch.refresh_from_db()
        self.assertEqual(batch.remaining_quantity, 8)

    def test_invalid_quantities_costs_and_insufficient_stock_leave_no_records(self):
        for values in ({'quantity':'0'}, {'quantity':'-1'}, {'quantity':'11'},
                       {'quantity':'NaN'}, {'quantity':'1.001'}, {'unit_cost':'-1'}):
            response = self.record('complimentary', **values)
            self.assertEqual(response.status_code, 400, response.data)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 10)
        self.assertFalse(StockAdjustment.objects.exists())

    def test_free_items_cannot_come_from_expired_or_disposed_stock(self):
        for status, expired in (('expired', False), ('disposed', False), ('available', True)):
            self.batch.status = status
            self.batch.expiration_date = timezone.localdate() + timedelta(days=-1 if expired else 7)
            self.batch.save()
            for kind in ('complimentary', 'free_taste'):
                self.assertEqual(self.record(kind).status_code, 400)
        self.assertFalse(StockAdjustment.objects.exists())
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 10)

    def test_filters_and_legacy_taste_test_rejection_remain_separate(self):
        for kind in ('spoilage', 'complimentary', 'free_taste', 'taste_test'):
            self.assertEqual(self.record(kind, quantity='1').status_code, 201)
        for kind in ('spoilage', 'complimentary', 'free_taste', 'taste_test'):
            response = self.client.get(self.url, {'adjustment_type':kind,
                'start_date':str(timezone.localdate()), 'end_date':str(timezone.localdate())})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.data), 1)
            self.assertEqual(response.data[0]['adjustment_type'], kind)
        for query in ({'adjustment_type':'invalid'}, {'start_date':'bad-date'},
                      {'start_date':'2026-10-02','end_date':'2026-10-01'}):
            self.assertEqual(self.client.get(self.url, query).status_code, 400)

    def test_visibility_and_authentication_apply_to_inventory_outflows(self):
        self.assertEqual(self.record('complimentary').status_code, 201)
        self.category.is_visible_to_staff = False
        self.category.save()
        self.assertEqual(self.record('free_taste').status_code, 400)
        self.assertEqual(self.client.get(self.url).data, [])
        self.client.force_authenticate(user=None)
        self.assertIn(self.record('free_taste').status_code, (401,403))

    def test_recorded_outflows_cannot_be_edited_or_deleted(self):
        response = self.record('complimentary')
        row_url = f'{self.url}{response.data["id"]}/'
        self.assertEqual(self.client.patch(row_url, {'quantity':'1'}, format='json').status_code, 405)
        self.assertEqual(self.client.delete(row_url).status_code, 405)

    def test_unknown_adjustment_type_is_rejected_in_the_service_too(self):
        with self.assertRaisesRegex(ValueError, 'Invalid adjustment_type'):
            BatchService.create_stock_adjustment('invalid', Decimal('1'), Decimal('25'), self.user, product_batch=self.batch)


class ProductConsolidationTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Dairy')
        self.original = self.product('BB300')
        self.duplicate = self.product('BB 300')

    def product(self, name, **overrides):
        fields = dict(name=name, category=self.category, unit='unit', shelf_life=7)
        fields.update(overrides)
        return Product.objects.create(**fields)

    def test_formatting_only_equivalence_preserves_meaningful_punctuation(self):
        self.assertEqual(product_name_key('FML (Pouch)'), product_name_key('fml pouch'))
        self.assertNotEqual(product_name_key('WC1.60kg'), product_name_key('WC160kg'))
        self.assertNotEqual(product_name_key('TM-CML'), product_name_key('TMCML'))

    def test_sizes_variants_units_and_pouch_remain_separate(self):
        others = [self.product('BB200'), self.product('BB300', variant='chocolate'),
                  self.product('BB300', unit='box'), self.product('BB300 Pouch')]
        consolidate_products()
        self.assertEqual(set(Product.objects.values_list('pk', flat=True)),
                         {self.original.pk, *(p.pk for p in others)})
        self.assertFalse(consolidation_plan())

    def test_moves_stock_orders_and_report_snapshots_without_changing_money(self):
        user = make_user('consolidation', role='admin')
        customer = Customer.objects.create(name='Customer', created_by=user)
        sale = Transaction.objects.create(handled_by=user, customer=customer, total_amount='17.00')
        order = Order.objects.create(customer=customer, handled_by=user, transaction=sale)
        batch = ProductBatch.objects.create(product=self.duplicate, batch_number='MERGE-1',
            initial_quantity='12', remaining_quantity='10', unit_price='5.00',
            expiration_date=timezone.localdate() + timedelta(days=7))
        item = OrderItem.objects.create(order=order, product=self.duplicate, quantity='2',
            unit_price='10', subtotal='20', discount_type='fixed', discount_value='3',
            discount_amount='3', total_amount='17')
        line = TransactionItem.objects.create(transaction=sale, product_batch=batch,
            quantity='2', unit_price='10', subtotal='20', discount_type='fixed',
            discount_value='3', discount_amount='3', total_amount='17',
            product_id_snapshot=self.duplicate.pk, product_name_snapshot=self.duplicate.name)
        with self.capture_commit_callbacks(execute=True):
            consolidate_products()
        for obj in (sale, batch, item, line):
            obj.refresh_from_db()
        self.assertFalse(Product.objects.filter(pk=self.duplicate.pk).exists())
        self.assertEqual(batch.product.pk, self.original.pk)
        self.assertEqual(batch.remaining_quantity, 10)
        self.assertEqual(batch.initial_quantity, 12)
        self.assertEqual(batch.unit_price, 5)
        self.assertEqual(item.product.pk, self.original.pk)
        self.assertEqual(line.product_batch.pk, batch.pk)
        self.assertEqual(line.product_id_snapshot, self.original.pk)
        self.assertEqual(line.product_name_snapshot, 'BB300')
        self.assertEqual(line.source_product_label, 'BB 300')
        for row in (line, item):
            self.assertEqual(row.quantity, 2)
            self.assertEqual(row.unit_price, 10)
            self.assertEqual(row.discount_amount, 3)
            self.assertEqual(row.total_amount, 17)
        self.assertEqual(sale.total_amount, 17)
        self.assertEqual(consolidate_products(), [])

    def test_cross_category_duplicates_keep_oldest_category_and_active_availability(self):
        other = Category.objects.create(name='Other')
        self.duplicate.category = other
        self.duplicate.save()
        self.original.is_active = False
        self.original.save()
        consolidate_products()
        self.original.refresh_from_db()
        self.assertEqual(self.original.category.pk, self.category.pk)
        self.assertTrue(self.original.is_active)

    def test_failure_rolls_back_every_merge(self):
        batch = ProductBatch.objects.create(product=self.duplicate, batch_number='MERGE-ROLLBACK',
            initial_quantity=5, remaining_quantity=5, expiration_date=timezone.localdate())
        # Order reassignment follows the batch move; a later failure must undo it.
        with patch.object(OrderItem.objects, 'filter', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                consolidate_products()
        self.assertEqual(Product.objects.count(), 2)
        batch.refresh_from_db()
        self.assertEqual(batch.product.pk, self.duplicate.pk)
        self.assertEqual(batch.remaining_quantity, 5)

    def test_api_rejects_duplicate_names_but_accepts_different_size(self):
        consolidate_products()
        fields = {'name': 'bb 300', 'unit': 'unit', 'category_id': self.category.pk, 'shelf_life': 7}
        serializer = ProductSerializer(data=fields)
        self.assertFalse(serializer.is_valid())
        self.assertIn('name', serializer.errors)
        fields['name'] = 'BB200'
        self.assertTrue(ProductSerializer(data=fields).is_valid())
        serializer = ProductSerializer(self.original, data={'name':'Bb 300'}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_original_import_label_is_preserved(self):
        user = make_user('mergeimport', role='admin')
        sale = Transaction.objects.create(handled_by=user)
        batch = ProductBatch.objects.create(product=self.duplicate, batch_number='MERGE-2',
            initial_quantity=0, remaining_quantity=0, expiration_date=timezone.localdate())
        line = TransactionItem.objects.create(transaction=sale, product_batch=batch, quantity=1,
            unit_price=10, source_product_label='Spreadsheet original label',
            product_id_snapshot=self.duplicate.pk, product_name_snapshot='BB 300')
        consolidate_products()
        line.refresh_from_db()
        self.assertEqual(line.source_product_label, 'Spreadsheet original label')
