"""Product-only inventory and safe retirement of the unused schema."""
from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, IntegrityError, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from config.test_support import APITestCase
from inventory.models import Category, Product, ProductBatch, StockAdjustment, StockCount


class ProductOnlyInventoryTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username='product-only-admin', email='product-only@example.test', role='admin')
        self.client.force_authenticate(self.user)
        self.product = Product.objects.create(
            name='Product-only milk', category=Category.objects.create(name='Product only'),
            unit='L', shelf_life=7)
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRODUCT-ONLY', initial_quantity=10,
            remaining_quantity=10, unit_price=50,
            expiration_date=timezone.localdate() + timedelta(days=7))

    def test_retired_lists_are_empty_and_cannot_be_written(self):
        for resource in ('ingredients', 'ingredient-batches', 'suppliers',
                         'low-stock/ingredients', 'expiring/ingredients'):
            url = f'/inventory/{resource}/'
            self.assertEqual(self.client.get(url).data, [])
            self.assertEqual(self.client.post(url, {'name': 'Obsolete'}, format='json').status_code, 410)
            self.assertEqual(self.client.get(f'{url}1/').status_code, 410)
            for method in (self.client.patch, self.client.put, self.client.delete):
                self.assertEqual(method(f'{url}1/', {}, format='json').status_code, 410)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/inventory/ingredients/').status_code, 401)
        for model in ('Ingredient', 'IngredientBatch', 'Supplier'):
            with self.assertRaises(LookupError):
                apps.get_model('inventory', model)

    def test_product_outflow_and_count_still_work(self):
        response = self.client.post('/inventory/stock-adjustments/', {
            'product_batch_id': self.batch.pk, 'adjustment_type': 'spoilage',
            'quantity': '2', 'unit_cost': '50',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['product_batch']['id'], self.batch.pk)
        self.assertNotIn('ingredient_batch', response.data)
        count = self.client.post('/inventory/stock-counts/', {
            'product_batch_id': self.batch.pk, 'counted_quantity': '7',
        }, format='json')
        self.assertEqual(count.status_code, 201, count.data)
        self.assertEqual(StockAdjustment.objects.count(), 2)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('7'))

    def test_missing_product_and_retired_targets_never_mutate_stock(self):
        for url, fields in (
            ('/inventory/stock-adjustments/', {'adjustment_type': 'spoilage', 'quantity': '1', 'unit_cost': '50'}),
            ('/inventory/stock-counts/', {'counted_quantity': '1'}),
        ):
            for target in ({}, {'ingredient_batch_id': 1},
                           {'product_batch_id': self.batch.pk, 'ingredient_batch_id': 1}):
                response = self.client.post(url, {**fields, **target}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(StockAdjustment.objects.exists())
        self.assertFalse(StockCount.objects.exists())
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('10'))

    def test_database_requires_a_product_for_movements(self):
        for model, fields in (
            (StockAdjustment, {'adjustment_type': 'spoilage', 'quantity': 1, 'unit_cost': 50, 'adjusted_by': self.user}),
            (StockCount, {'expected_quantity': 10, 'counted_quantity': 9, 'variance': -1, 'counted_by': self.user}),
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.create(product_batch=None, **fields)


class ProductOnlyMigrationTests(TransactionTestCase):
    old = [('inventory', '0011_alter_stockadjustment_adjustment_type')]
    new = [('inventory', '0012_product_only_inventory')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.old)
        self.old_apps = executor.loader.project_state(self.old).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.old)
        for name in ('StockCount', 'StockAdjustment', 'IngredientBatch', 'Ingredient', 'Supplier'):
            self.old_apps.get_model('inventory', name).objects.all().delete()
        MigrationExecutor(connection).migrate(self.new)
        super().tearDown()

    def test_empty_retired_schema_is_removed_and_product_stock_preserved(self):
        category = self.old_apps.get_model('inventory', 'Category').objects.create(name='Migration dairy')
        product = self.old_apps.get_model('inventory', 'Product').objects.create(
            name='Migration milk', category=category, unit='L', shelf_life=7)
        batch = self.old_apps.get_model('inventory', 'ProductBatch').objects.create(
            product=product, batch_number='MIGRATION-PRODUCT', initial_quantity=10,
            remaining_quantity=8, unit_price=50,
            expiration_date=timezone.localdate() + timedelta(days=7))
        MigrationExecutor(connection).migrate(self.new)
        self.assertEqual(ProductBatch.objects.get(pk=batch.pk).remaining_quantity, Decimal('8'))
        tables = connection.introspection.table_names()
        for table in ('inventory_ingredient', 'inventory_ingredientbatch', 'inventory_supplier'):
            self.assertNotIn(table, tables)

    def test_existing_legacy_records_abort_before_any_schema_is_removed(self):
        for name, fields in (
            ('Supplier', {'name': 'Preserve supplier'}),
            ('Ingredient', {'name': 'Preserve ingredient', 'unit': 'L', 'unit_price': 20, 'shelf_life': 7}),
        ):
            model = self.old_apps.get_model('inventory', name)
            record = model.objects.create(**fields)
            with self.assertRaisesMessage(RuntimeError, 'cleanup stopped'):
                MigrationExecutor(connection).migrate(self.new)
            self.assertTrue(model.objects.filter(pk=record.pk).exists())
            self.assertIn('inventory_ingredientbatch', connection.introspection.table_names())
            record.delete()
