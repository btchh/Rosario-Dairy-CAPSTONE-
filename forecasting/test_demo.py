"""Retired synthetic entry points stay absent; archived evidence stays intact."""
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command, CommandError, get_commands
from django.test import TestCase
from rest_framework.test import APIClient

from .models import ForecastExperiment


class RetiredSyntheticForecastTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_retired_command_is_unavailable(self):
        self.assertNotIn('evaluate_demo_forecasts', get_commands())
        with self.assertRaisesRegex(CommandError, 'Unknown command'):
            call_command('evaluate_demo_forecasts', stdout=StringIO())

    def test_retired_endpoint_does_not_read_cached_synthetic_results(self):
        admin = get_user_model().objects.create_user(
            username='actual-admin', email='actual-admin@example.test', role='admin')
        client = APIClient()
        client.force_authenticate(admin)
        with patch('config.data_cache.get_payload', return_value={'status': 'test_only', 'components': {}}) as cached:
            for method in (client.get, client.post, client.put, client.patch, client.delete):
                self.assertEqual(method('/api/forecasting/demo-2026/').status_code, 404)
        cached.assert_not_called()
        self.assertFalse(ForecastExperiment.objects.exists())

    def test_removed_endpoint_does_not_delete_archived_experiments(self):
        archived = ForecastExperiment.objects.create(
            version='archived', source_revision='archived',
            findings={'components': {'regular': {'metrics': {'wape_percent': 1}}}})
        self.assertEqual(APIClient().get('/api/forecasting/demo-2026/').status_code, 404)
        archived.refresh_from_db()
        self.assertEqual(archived.findings['components']['regular']['metrics']['wape_percent'], 1)
