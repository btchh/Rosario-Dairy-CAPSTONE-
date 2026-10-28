"""Corrupt portable SARIMA state must withhold approval and never reach filtering."""
from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .accuracy import monthly_accuracy
from .learning import ENGINE_VERSION, digest, fit_model, shift_month
from .lifecycle import quality_gate
from .seasonal import SARIMA_CONFIGS, is_sarima_artifact, predict_sarima
from .serving import model_context, models_report


class SarimaArtifactValidationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rows = [{'month': shift_month(date(2023, 1, 1), i).isoformat(),
                     'revenue': 1000 + 7 * i + 15 * (i % 12)} for i in range(36)]
        cls.states = {name: fit_model(name, cls.rows) for name in SARIMA_CONFIGS}

    def state(self):
        return deepcopy(self.states['sarima_010_100_12'])

    def model_run(self, artifact, checksum=None):
        return SimpleNamespace(pk=1, status='research_candidate', engine_version=ENGINE_VERSION,
            trained_through=date(2025, 12, 31), findings={
                'status': 'research_candidate', 'artifact': artifact,
                'artifact_sha256': checksum if checksum is not None else digest(artifact),
                'selected_candidate': 'sarima_010_100_12',
                'next_month': {'date': '2026-01-01'},
                'retrospective_gate': {'below_15_percent': True, 'no_worse_than_baseline': True}})

    def gate(self, run):
        coverage = {shift_month(date(2025, 1, 1), i): object() for i in range(12)}
        evidence = {'metrics': {'periods': 12, 'wape_percent': 1},
                    'baseline_metrics': {'periods': 12, 'wape_percent': 2},
                    'months': [month.isoformat() for month in coverage], 'rows': []}
        with patch('forecasting.lifecycle.prospective_evidence', return_value=evidence), \
                patch('forecasting.lifecycle.timezone.localdate', return_value=date(2026, 1, 1)):
            return quality_gate(run, SimpleNamespace(as_of=date(2026, 1, 1)), coverage)

    def corrupt_states(self):
        mutations = {
            'missing_scale': {'scale': None}, 'zero_scale': {'scale': 0},
            'negative_scale': {'scale': -1}, 'string_scale': {'scale': '1'},
            'boolean_scale': {'scale': True}, 'null_history': {'normalized_history': None},
            'object_history': {'normalized_history': {}}, 'empty_history': {'normalized_history': []},
            'float_count': {'training_months': 36.0}, 'boolean_count': {'training_months': True},
            'short_count': {'training_months': 17}, 'wrong_count': {'training_months': 35},
            'null_params': {'params': None}, 'object_params': {'params': {}},
            'string_params': {'params': 'xy'}, 'empty_params': {'params': []},
            'nonfinite_string_params': {'params': ['NaN', 'Infinity']},
            'boolean_params': {'params': [True, False]},
            'negative_variance': {'params': [0.5, -0.1]},
            'wrong_parameter_count': {'params': [0.0], 'param_names': ['invented']},
            'wrong_parameter_order': {'param_names': ['sigma2', 'ar.S.L12']},
            'null_names': {'param_names': None}, 'unhashable_name': {'name': []},
        }
        for label, changes in mutations.items():
            state = self.state()
            state.update(changes)
            if label == 'missing_scale':
                del state['scale']
            yield label, json.loads(json.dumps(state, allow_nan=False))
        for value in (None, True, -1, 'NaN'):
            state = self.state()
            state['normalized_history'][0] = value
            yield f'history_value_{value!r}', state

    def test_all_actual_fits_survive_json_and_reproduce_predictions_without_optimization(self):
        for name, original in self.states.items():
            with self.subTest(candidate=name):
                state = json.loads(json.dumps(original, allow_nan=False))
                with patch('forecasting.seasonal.SARIMAX.fit', side_effect=AssertionError('must not fit')):
                    self.assertTrue(is_sarima_artifact(state))
                    self.assertAlmostEqual(predict_sarima(state), original['fitted_next_value'], places=7)
        self.assertTrue(self.gate(self.model_run(self.state()))['eligible'])

    def test_zero_innovation_variance_remains_permitted(self):
        artifact = self.state()
        artifact['params'][-1] = 0.0
        self.assertTrue(is_sarima_artifact(artifact))
        self.assertTrue(self.gate(self.model_run(artifact))['eligible'])

    def test_json_corruption_fails_closed_even_when_its_checksum_matches(self):
        for label, artifact in self.corrupt_states():
            with self.subTest(corruption=label):
                self.assertFalse(is_sarima_artifact(artifact))
                gate = self.gate(self.model_run(artifact))
                self.assertFalse(gate['eligible'])
                self.assertIn('primary_model_must_be_sarima', gate['reasons'])
                with patch('forecasting.seasonal.SARIMAX.filter') as filtering:
                    with self.assertRaisesRegex(ValueError, 'Invalid fitted SARIMA artifact'):
                        predict_sarima(artifact)
                    filtering.assert_not_called()

    def test_nonfinite_or_nonportable_artifacts_cannot_crash_hashing_or_reach_filter(self):
        for field, value in [('scale', float('inf')), ('scale', 10 ** 1000),
                             ('params', [float('nan'), 1]),
                             ('normalized_history', [float('inf')] * 36)]:
            with self.subTest(field=field, value_type=type(value).__name__):
                artifact = self.state()
                artifact[field] = value
                self.assertFalse(is_sarima_artifact(artifact))
                gate = self.gate(self.model_run(artifact, checksum='original-unmodified-checksum'))
                self.assertFalse(gate['eligible'])
                with patch('forecasting.seasonal.SARIMAX.filter') as filtering:
                    with self.assertRaises(ValueError):
                        predict_sarima(artifact)
                    filtering.assert_not_called()

    def test_non_mapping_artifacts_are_rejected_without_exceptions(self):
        for artifact in (None, [], 'not fitted state', 0, True):
            with self.subTest(artifact=artifact):
                self.assertFalse(is_sarima_artifact(artifact))
                self.assertFalse(self.gate(self.model_run(artifact))['eligible'])

    def test_corrupt_history_with_original_checksum_is_withheld_by_read_paths(self):
        artifact = self.state()
        original_checksum = digest(artifact)
        artifact['normalized_history'] = None
        run = self.model_run(artifact, checksum=original_checksum)
        snapshot = SimpleNamespace(as_of=date(2026, 1, 1), revision='mocked-snapshot')
        evidence = {'metrics': {'periods': 0, 'wape_percent': None},
                    'baseline_metrics': {'periods': 0, 'wape_percent': None}, 'months': [], 'rows': []}
        with patch('forecasting.lifecycle.prospective_evidence', return_value=evidence), \
                patch('forecasting.lifecycle.timezone.localdate', return_value=date(2026, 1, 1)), \
                patch('forecasting.serving._snapshot', return_value=snapshot), \
                patch('forecasting.serving.valid_coverage', return_value={}), \
                patch('forecasting.serving.MonthlyModelRun.objects.filter') as runs, \
                patch('forecasting.serving.MonthlyDeployment.objects.filter') as deployments, \
                patch('forecasting.serving.MonthlyIssuedForecast.objects.filter') as issues, \
                patch('forecasting.accuracy.valid_coverage', return_value={}), \
                patch('forecasting.accuracy.coverage_rows', return_value=self.rows):
            runs.return_value.first.return_value = run
            deployments.return_value.first.return_value = None
            issues.return_value.first.return_value = None
            *_, gate, issued, status = model_context()
            self.assertEqual(status, 'research_only')
            self.assertFalse(gate['eligible'])
            self.assertIsNone(issued)
            self.assertEqual(monthly_accuracy(snapshot)['status'], 'invalid_comparison')

    def test_model_status_metadata_tolerates_missing_or_non_mapping_artifact(self):
        for artifact in (None, [], 'not fitted state'):
            with self.subTest(artifact=artifact):
                run = self.model_run(artifact)
                snapshot = SimpleNamespace(as_of=date(2026, 1, 1), revision='source')
                gate = self.gate(run)
                with patch('forecasting.serving.model_context', return_value=(
                        snapshot, run, run, gate, None, 'research_only')), \
                        patch('forecasting.serving.MonthlyModelRun.objects.values', return_value=[]):
                    result = models_report()
                self.assertEqual(result['status'], 'research_only')
                self.assertFalse(result['quality_gate']['eligible'])
                self.assertTrue(all(value is None for value in result['sarima'].values()))
