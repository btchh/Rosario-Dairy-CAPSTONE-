import csv
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class EvaluationExportTests(SimpleTestCase):
    def run_fixture(self):
        score = {'mape_percent': None, 'wape_percent': None,
                 'coverage_percent': None, 'mae_pesos': None, 'rows': 0}
        metrics = {name: dict(score) for name in ('regular', 'bulk', 'combined')}
        metrics.update({f'{name}_baselines': {} for name in ('regular', 'bulk', 'combined')})
        period = {'metrics': metrics, 'rows': [], 'status': 'insufficient_history',
                  'next_period': {'method': 'sarima', 'bulk': {'weighting': 'mean'}}}
        return SimpleNamespace(result={
            'selected_setup': None, 'selection': [],
            'setups': {'fixed': {'setup': 'fixed', 'periods': {'monthly': period}}},
        })

    def test_routine_refresh_does_not_export_private_revenue_files(self):
        with patch('forecasting.management.commands.evaluate_forecast.refresh',
                   return_value=(self.run_fixture(), True)) as refresh, \
             patch.object(Path, 'mkdir') as mkdir, \
             patch.object(Path, 'write_text') as write_text, \
             patch.object(Path, 'open') as open_file:
            call_command('evaluate_forecast', stdout=StringIO())
        refresh.assert_called_once()
        mkdir.assert_not_called()
        write_text.assert_not_called()
        open_file.assert_not_called()

    def test_explicit_private_export_still_includes_comparison_and_protocol(self):
        with TemporaryDirectory() as directory, patch(
            'forecasting.management.commands.evaluate_forecast.refresh',
            return_value=(self.run_fixture(), True),
        ):
            folder = Path(directory) / 'evaluation'
            call_command('evaluate_forecast', output=folder, stdout=StringIO())
            with (folder / 'comparison.csv').open() as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 3)
            self.assertEqual({row['status'] for row in rows}, {'insufficient_history'})
            self.assertEqual(json.loads((folder / 'real.json').read_text())['selected_setup'], None)
            self.assertIn('limitations', json.loads((folder / 'protocol.json').read_text()))
            self.assertTrue((folder / 'limitations.txt').is_file())
