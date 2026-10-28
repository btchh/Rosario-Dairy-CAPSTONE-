from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import statsmodels
from django.test import SimpleTestCase

from .data import RecordedSale, SalesSnapshot
from .graph_sarima import regular_weekly_component, regular_weekly_dependency
from .learning import digest
from .provisional import future_rows
from .weekly import CONFIGS, VERSION, _parameter_names, train_weekly, weekly_rows


def snapshot():
    return SalesSnapshot(date(2026, 10, 26), 'unchanged-source', tuple(
        RecordedSale(index, date(2023, 1, 4) + timedelta(weeks=index), 'customer', 'Customer',
                     'regular', Decimal(str(1000 + index % 13 * 20)))
        for index in range(156)), 0)


def findings(rows):
    def fit(name, history):
        names = _parameter_names(name)
        state = {'name': name, 'family': 'SARIMA', 'converged': True,
                 **{key: list(value) if isinstance(value, tuple) else value
                    for key, value in CONFIGS[name].items()},
                 'training_weeks': len(history), 'normalized_history': [row['revenue'] for row in history],
                 'scale': 1, 'statsmodels_version': statsmodels.__version__,
                 'params': [1. if key == 'sigma2' else .1 for key in names], 'param_names': names,
                 'trained_through': history[-1]['date'],
                 'target_week': (date.fromisoformat(history[-1]['date'])+timedelta(days=7)).isoformat()}
        return state, history[-1]['revenue'] + list(CONFIGS).index(name) * 25
    with patch('forecasting.weekly.fit_weekly', side_effect=fit):
        result = train_weekly(rows)
    result['training_signature'] = digest(rows)
    return result


def run_for(s):
    rows = weekly_rows(s)
    return SimpleNamespace(pk=7, source_revision=s.revision, cutoff=s.as_of,
                           trained_through=date.fromisoformat(rows[-1]['date'])+timedelta(days=6),
                           status='research_candidate', findings=findings(rows))


class GraphWeeklySarimaTests(SimpleTestCase):
    def dependency(self, s=None, run=None):
        s = s or snapshot()
        with patch('forecasting.graph_sarima.WeeklyModelRun.objects.filter') as query:
            query.return_value.first.return_value = run
            result = regular_weekly_dependency(s)
        return result, query

    def test_saved_fit_replays_without_optimization_and_keeps_horizon_evidence_separate(self):
        s = snapshot()
        run = run_for(s)
        with patch('forecasting.graph_sarima.train_weekly', side_effect=AssertionError('No fit')):
            dependency, query = self.dependency(s, run)
            component = regular_weekly_component(dependency, allow_training=False)
        self.assertEqual(dependency['status'], 'saved_sarima')
        self.assertEqual(query.call_args.kwargs['source_revision'], s.revision)
        self.assertEqual(component['primary_model_id'], 7)
        self.assertIsNone(component['test'])
        self.assertEqual(component['primary_weekly_backtest']['basis'], 'rolling_one_week_ahead')
        self.assertEqual(component['primary_weekly_backtest']['metrics'], run.findings['selected_test'])
        with patch('forecasting.weekly.SARIMAX.fit', side_effect=AssertionError('No optimization')):
            forecast = future_rows(component, ['2026-10-26', '2026-11-02'], 'weekly')
        self.assertEqual(len(forecast), 2)
        self.assertTrue(all(row['basis'] == 'single_origin_sarima' for row in forecast))

    def test_partial_source_boundary_is_not_used_as_an_annual_seasonal_training_week(self):
        s = snapshot()
        s = replace(s, sales=(*s.sales, RecordedSale(999, date(2025, 12, 31), 'customer', 'Customer',
                                                   'regular', Decimal('999999'))))
        dependency, _ = self.dependency(s, run_for(s))
        self.assertEqual(len(dependency['rows']), 156)
        self.assertEqual(dependency['state']['trained_through'], '2025-12-22')
        self.assertEqual(dependency['rows'][-1]['date'], '2025-12-22')

    def test_retained_later_cutoff_can_reconstruct_only_its_exact_earlier_history(self):
        s = snapshot()
        run = run_for(s)
        historical = replace(s, as_of=date(2026, 10, 5))
        dependency, _ = self.dependency(historical, run)
        self.assertEqual(dependency['status'], 'saved_sarima')
        self.assertEqual(dependency['dependency']['cutoff'], '2026-10-26')
        self.assertEqual(dependency['dependency']['snapshot_cutoff'], '2026-10-05')

    def test_tampered_or_mismatched_saved_run_blocks_training_and_fallback(self):
        s = snapshot()
        original = run_for(s)
        variants = []
        run = deepcopy(original); run.findings['artifact']['params'][0] += .01; variants.append(run)
        run = deepcopy(original); run.findings['training_signature'] = 'changed'; variants.append(run)
        run = deepcopy(original); run.trained_through -= timedelta(days=7); variants.append(run)
        run = deepcopy(original); run.trained_through = None; variants.append(run)
        run = deepcopy(original); run.status = 'selected_model_failed'; variants.append(run)
        run = deepcopy(original); run.findings = []; variants.append(run)
        for run in variants:
            with self.subTest(run=str(run.findings)[:30]), patch(
                    'forecasting.graph_sarima.train_weekly', side_effect=AssertionError('No repair-by-fit')):
                dependency, _ = self.dependency(s, run)
                self.assertEqual(dependency['status'], 'invalid_saved_artifact')
                self.assertNotIn('state', regular_weekly_component(dependency))

    def test_rehashed_wrong_history_or_schema_still_rejected(self):
        s = snapshot()
        for key, value in (('normalized_history', [100.] * 156),
                           ('param_names', ['wrong', 'schema']), ('statsmodels_version', '0.0.0')):
            run = run_for(s)
            run.findings['artifact'][key] = value
            run.findings['artifact_sha256'] = digest(run.findings['artifact'])
            dependency, _ = self.dependency(s, run)
            self.assertEqual(dependency['status'], 'invalid_saved_artifact')

    def test_selection_is_bound_to_earlier_validation_not_better_later_test_score(self):
        s = snapshot()
        run = run_for(s)
        selected = run.findings['selected_candidate']
        run.findings['candidates'][selected]['validation']['wape_percent'] = 0
        dependency, _ = self.dependency(s, run)
        self.assertEqual(dependency['status'], 'invalid_saved_artifact')

    def test_missing_saved_fit_trains_only_during_offline_refresh(self):
        s = snapshot()
        dependency, _ = self.dependency(s)
        with patch('forecasting.graph_sarima.train_weekly', side_effect=AssertionError('No GET fit')):
            result = regular_weekly_component(dependency, allow_training=False)
        self.assertEqual(result['status'], 'primary_refresh_required')
        with patch('forecasting.graph_sarima.train_weekly', return_value=findings(weekly_rows(s))) as train:
            result = regular_weekly_component(dependency)
        train.assert_called_once_with(weekly_rows(s))
        self.assertEqual(result['selection'], 'earlier_validation_weekly_sarima_selection')
        self.assertEqual(result['state']['trained_through'], '2025-12-22')

    def test_insufficient_history_does_not_query_or_claim_annual_seasonal_support(self):
        s = snapshot()
        s = replace(s, sales=s.sales[-100:])
        with patch('forecasting.graph_sarima.WeeklyModelRun.objects.filter',
                   side_effect=AssertionError('No saved annual fit can match')):
            dependency = regular_weekly_dependency(s)
        result = regular_weekly_component(dependency)
        self.assertEqual(result['status'], 'fallback_allowed')
        self.assertFalse(result['seasonal_terms_enabled'])
        self.assertIsNone(result['test'])
