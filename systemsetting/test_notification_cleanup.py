"""Removing inactive flags preserves the three working alert preferences."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class NotificationFlagMigrationTests(TransactionTestCase):
    old = [('systemsetting', '0002_notificationstate')]
    new = [('systemsetting', '0003_remove_inactive_notification_flags')]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_cleanup_preserves_existing_alert_preferences(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.old)
        old_model = executor.loader.project_state(self.old).apps.get_model(
            'systemsetting', 'NotificationSettings')
        config = old_model.objects.create(
            pk=1, low_stock_alerts=False, near_expiry_alerts=True,
            new_order_alerts=False, forecast_warnings=False,
            report_ready_notifications=True)

        executor = MigrationExecutor(connection)
        executor.migrate(self.new)
        new_model = executor.loader.project_state(self.new).apps.get_model(
            'systemsetting', 'NotificationSettings')
        current = new_model.objects.get(pk=config.pk)
        self.assertFalse(current.low_stock_alerts)
        self.assertTrue(current.near_expiry_alerts)
        self.assertFalse(current.new_order_alerts)
        self.assertEqual(current.updated_at, config.updated_at)
        with connection.cursor() as cursor:
            columns = {
                field.name for field in connection.introspection.get_table_description(
                    cursor, new_model._meta.db_table)
            }
        self.assertNotIn('forecast_warnings', columns)
        self.assertNotIn('report_ready_notifications', columns)
