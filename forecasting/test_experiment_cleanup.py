from datetime import date
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ExperimentCleanupMigrationTests(TransactionTestCase):
    def test_migration_removes_experiments_and_preserves_production(self):
        executor = MigrationExecutor(connection)
        before = [('forecasting', '0002_simulationrun_simulatedsalesday')]
        after = [('forecasting', '0003_remove_experiment_storage')]
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            runs = apps.get_model('forecasting', 'ForecastRun').objects
            common = dict(configuration_signature='config', source_signature='source',
                          data_end=date(2025, 12, 31), result={})
            production = runs.create(scope='real', version='daily-sarima-v1', **common)
            for version in ('segmented-sarima-v1', 'tuned-sarima-v1', 'customer-sarima-v1'):
                runs.create(scope='real', version=version, **common)
            simulation = apps.get_model('forecasting', 'SimulationRun').objects.create(
                target_year=2026, seed=2026, method='test', source_signature='source', metadata={})
            apps.get_model('forecasting', 'SimulatedSalesDay').objects.create(
                run=simulation, date=date(2026, 1, 1), feeding_revenue=10, other_sales_revenue=20)

            executor = MigrationExecutor(connection)
            executor.migrate(after)
            apps = executor.loader.project_state(after).apps
            self.assertEqual(list(apps.get_model('forecasting', 'ForecastRun').objects.values_list(
                'pk', flat=True)), [production.pk])
            tables = connection.introspection.table_names()
            self.assertNotIn('forecasting_simulationrun', tables)
            self.assertNotIn('forecasting_simulatedsalesday', tables)
        finally:
            MigrationExecutor(connection).migrate(after)
