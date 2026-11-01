"""Retire empty stock-count storage without touching stock or count history."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class StockCountRetirementMigrationTests(TransactionTestCase):
    old = [('inventory', '0013_stock_adjustment_submission')]
    new = [('inventory', '0014_remove_obsolete_stock_count')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.old)
        self.old_apps = executor.loader.project_state(self.old).apps
        category = self.old_apps.get_model('inventory', 'Category').objects.create(
            name='Retirement dairy')
        product = self.old_apps.get_model('inventory', 'Product').objects.create(
            name='Retirement milk', category=category, unit='L', shelf_life=7)
        self.batch = self.old_apps.get_model('inventory', 'ProductBatch').objects.create(
            product=product, batch_number='RETIRE-COUNT', initial_quantity=10,
            remaining_quantity=8, unit_price=50,
            expiration_date=timezone.localdate() + timedelta(days=7))

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.old)
        self.old_apps.get_model('inventory', 'StockCount').objects.all().delete()
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_empty_count_table_is_removed_without_changing_stock_or_adjustments(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.new)
        current = executor.loader.project_state(self.new).apps
        self.assertNotIn('inventory_stockcount', connection.introspection.table_names())
        batch = current.get_model('inventory', 'ProductBatch').objects.get(pk=self.batch.pk)
        self.assertEqual(batch.remaining_quantity, self.batch.remaining_quantity)
        self.assertEqual(batch.initial_quantity, self.batch.initial_quantity)
        self.assertFalse(current.get_model('inventory', 'StockAdjustment').objects.exists())

    def test_existing_count_records_block_removal_and_preserve_linked_adjustments(self):
        # Only inventory is rolled back; the accounts table keeps its current schema.
        user = get_user_model().objects.create_user(
            username='count-history', email='count-history@example.test', role='admin')
        adjustment = self.old_apps.get_model('inventory', 'StockAdjustment').objects.create(
            product_batch=self.batch, adjustment_type='correction', quantity=2,
            unit_cost=50, adjusted_by_id=user.pk)
        model = self.old_apps.get_model('inventory', 'StockCount')
        count = model.objects.create(
            product_batch=self.batch, expected_quantity=10, counted_quantity=8,
            variance=-2, counted_by_id=user.pk, resulting_adjustment=adjustment)

        with self.assertRaisesMessage(RuntimeError, 'Stock Count removal stopped'):
            MigrationExecutor(connection).migrate(self.new)

        self.assertIn('inventory_stockcount', connection.introspection.table_names())
        self.assertTrue(model.objects.filter(pk=count.pk, resulting_adjustment=adjustment).exists())
        self.assertTrue(self.old_apps.get_model('inventory', 'StockAdjustment').objects.filter(
            pk=adjustment.pk).exists())
