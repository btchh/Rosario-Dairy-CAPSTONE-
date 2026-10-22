"""Guard the actual-invoices-only policy and retain archived experiment rows."""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command, CommandError
from django.test import TestCase
from rest_framework.test import APIClient

from .demo import load_inputs, refresh_demo, demo_report
from .models import ForecastExperiment


class DisabledSyntheticForecastTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_input_and_training_entry_points_cannot_use_synthetic_sales(self):
        with patch('forecasting.learning.train', side_effect=AssertionError('must not fit')):
            for entry in (load_inputs, refresh_demo):
                with self.assertNumQueries(0), self.assertRaisesRegex(ValueError, 'disabled'):
                    entry()

    def test_report_withholds_archived_scores_without_reading_them(self):
        archived = ForecastExperiment.objects.create(version='archived', source_revision='archived',
            findings={'components': {'regular': {'metrics': {'wape_percent': 1}}}})
        with self.assertNumQueries(0):
            result = demo_report(archived)
        self.assertEqual(result['status'], 'disabled')
        self.assertEqual(result['forecast'], [])
        self.assertNotIn('components', result)
        archived.refresh_from_db()
        self.assertEqual(archived.findings['components']['regular']['metrics']['wape_percent'], 1)

    def test_command_fails_without_exporting_or_fitting(self):
        with TemporaryDirectory() as folder:
            output = Path(folder) / 'results'
            with self.assertNumQueries(0), self.assertRaisesRegex(CommandError, 'actual invoices only'):
                call_command('evaluate_demo_forecasts', output=output, stdout=StringIO())
            self.assertFalse(output.exists())

    def test_admin_endpoint_returns_gone_and_does_not_use_cached_results(self):
        admin = get_user_model().objects.create_user(username='actual-admin', email='actual-admin@example.test', role='admin')
        client = APIClient(); client.force_authenticate(admin)
        with patch('config.data_cache.get_payload', return_value={'status': 'test_only', 'components': {}}) as cached:
            response = client.get('/api/forecasting/demo-2026/')
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.data['status'], 'disabled')
        self.assertNotIn('components', response.data)
        cached.assert_not_called()
        self.assertFalse(ForecastExperiment.objects.exists())
