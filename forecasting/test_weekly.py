from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
import json
import statsmodels
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase

from config.test_support import APITestCase
from .benchmarks import score
from .data import RecordedSale, SalesSnapshot
from .learning import digest
from .models import WeeklyModelRun, MonthlyModelRun, MonthlyIssuedForecast, MonthlyDeployment
from .weekly import (CONFIGS, VERSION, MIN_WEEKS, fit_weekly, predict_weekly, weekly_rows,
                     refresh_weekly, weekly_report, train_weekly, walk_weekly, weekly_snapshot,
                     _parameter_names, valid_weekly_artifact)


def observations(count=156):
    return [{'date': (date(2023, 1, 2)+timedelta(weeks=i)).isoformat(), 'revenue': 1000.+i%13*20}
            for i in range(count)]


def snapshot():
    return SalesSnapshot(date(2026, 1, 5), 'weekly-revision', tuple(
        RecordedSale(i, date.fromisoformat(r['date'])+timedelta(days=2), 'customer', 'Customer',
                     'regular', Decimal(str(r['revenue']))) for i, r in enumerate(observations())), 9)


def artifact(name, rows):
    spec = CONFIGS[name]
    return {'name': name, 'family': 'SARIMA', 'converged': True,
        'order': list(spec['order']), 'seasonal_order': list(spec['seasonal_order']), 'trend': spec['trend'],
        'training_weeks': len(rows), 'normalized_history': [r['revenue'] for r in rows],
        'params': [1 if key == 'sigma2' else .1 for key in _parameter_names(name)],
        'statsmodels_version': statsmodels.__version__,
        'scale': 1, 'trained_through': rows[-1]['date'],
        'target_week': (date.fromisoformat(rows[-1]['date'])+timedelta(days=7)).isoformat()}


def fixture_train(rows):
    name = next(iter(CONFIGS))
    state = artifact(name, rows)
    folds = [{'date': r['date'], 'trained_through': rows[i-1]['date'],
              'actual': r['revenue'], 'predicted': r['revenue']-10}
             for i, r in enumerate(rows) if i >= len(rows)-13]
    return {'status': 'research_candidate', 'engine_version': VERSION, 'selected_candidate': name,
        'artifact': state, 'artifact_sha256': digest(state), 'selected_test': score(folds),
        'next_week': {'date': state['target_week'], 'predicted_revenue': 1200},
        'candidates': {name: {'test_rows': folds}}}


class WeeklyResearchTests(SimpleTestCase):
    def test_saved_schema_rejects_corrupt_parameters_versions_and_nonstationary_coefficients(self):
        state = artifact('weekly_sarima_100_100_52', observations())
        self.assertTrue(valid_weekly_artifact(state))
        mutations = [
            ('params', state['params'][:-1]), ('param_names', ['wrong'] * len(state['params'])),
            ('statsmodels_version', '0.0.0'), ('training_weeks', True), ('scale', True),
            ('normalized_history', [True] * 156), ('params', [False] * len(state['params'])),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=str(value)[:30]):
                self.assertFalse(valid_weekly_artifact({**state, key: value}))
        for key in ('ar.L1', 'ar.S.L52', 'sigma2'):
            values = state['params'].copy()
            values[_parameter_names(state['name']).index(key)] = -1 if key == 'sigma2' else 1.01
            self.assertFalse(valid_weekly_artifact({**state, 'params': values}))

    def test_full_weeks_use_latest_contiguous_real_regular_span_and_drop_boundary_weeks(self):
        s = snapshot()
        other = RecordedSale(999, date(2025, 12, 10), 'feeding', 'Feeding', 'feeding', Decimal('99999'))
        unknown = replace(other, id=1000, segment='unclassified')
        rows = weekly_rows(replace(s, sales=(*s.sales, other, unknown)))
        self.assertEqual(rows, observations())
        self.assertEqual(len(rows), 156)
        # A recorded zero still represents an observation, unlike a missing week.
        missing = replace(s, sales=tuple(v for v in s.sales if v.id != 140))
        self.assertEqual(len(weekly_rows(missing)), 15)
        zero = replace(s, sales=tuple(replace(v, revenue=Decimal('0')) if v.id == 140 else v for v in s.sales))
        self.assertEqual(len(weekly_rows(zero)), 156)

    def test_insufficient_history_never_invokes_a_fit(self):
        with patch('forecasting.weekly.fit_weekly', side_effect=AssertionError('No fit')):
            result = train_weekly(observations(MIN_WEEKS-1))
        self.assertEqual(result['status'], 'insufficient_history')
        self.assertEqual(result['required_weeks'], 130)

    def test_walk_forward_never_passes_target_or_later_rows_to_fitter(self):
        rows = observations(132)
        def fit(name, history):
            self.assertLess(history[-1]['date'], rows[104+len(seen)]['date'])
            seen.append(deepcopy(history))
            return {}, history[-1]['revenue']
        seen = []
        with patch('forecasting.weekly.fit_weekly', side_effect=fit):
            folds = walk_weekly(next(iter(CONFIGS)), rows, 104, 107)
        self.assertEqual([len(h) for h in seen], [104, 105, 106])
        self.assertEqual(folds[0]['date'], rows[104]['date'])

    def test_test_values_cannot_reselect_validation_winner(self):
        names = list(CONFIGS)
        def fit(name, history):
            value = 1000 if name == names[0] else 1100
            return artifact(name, history), value
        rows = observations(130)
        for r in rows:r['revenue'] = 1000
        with patch('forecasting.weekly.fit_weekly', side_effect=fit):
            first = train_weekly(rows)
            for r in rows[-13:]:r['revenue'] = 1100
            second = train_weekly(rows)
        self.assertEqual(first['selected_candidate'], names[0])
        self.assertEqual(second['selected_candidate'], names[0])
        self.assertGreater(second['selected_test']['wape_percent'], second['candidates'][names[1]]['test']['wape_percent'])

    def test_selected_model_failure_has_no_baseline_or_test_winner_fallback(self):
        names = list(CONFIGS)
        def fit(name, history):
            if name == names[0] and len(history) >= 117:
                raise ValueError('Selected test fit failed')
            return artifact(name, history), 1000 if name == names[0] else 1100
        rows = observations(130)
        for r in rows:r['revenue'] = 1000
        with patch('forecasting.weekly.fit_weekly', side_effect=fit):
            result = train_weekly(rows)
        self.assertEqual(result['status'], 'selected_model_failed')
        self.assertEqual(result['selected_candidate'], names[0])

    def test_real_sarima_state_round_trips_and_predicts_without_optimization(self):
        state, value = fit_weekly(next(iter(CONFIGS)), observations(110))
        saved = json.loads(json.dumps(state, allow_nan=False))
        with patch('forecasting.weekly.SARIMAX.fit', side_effect=AssertionError('No optimization on replay')):
            self.assertAlmostEqual(predict_weekly(saved), value, places=2)
        saved['seasonal_order'][-1] = 12
        with self.assertRaisesRegex(ValueError, 'Invalid weekly'):
            predict_weekly(saved)

    def test_weekly_cutoff_is_monday_and_excludes_current_partial_week(self):
        with patch('forecasting.weekly.load_snapshot', return_value=None) as load:
            weekly_snapshot(date(2025, 1, 1))
        self.assertEqual(load.call_args.args[0], date(2024, 12, 30))


class WeeklyLifecycleTests(APITestCase):
    def setUp(self):
        cache.clear()

    def refresh(self, s=None):
        with patch('forecasting.weekly.weekly_snapshot', return_value=s or snapshot()), \
                patch('forecasting.weekly.train_weekly', side_effect=fixture_train):
            return refresh_weekly()

    def test_reuses_exact_inputs_and_retains_new_versions_after_corrections(self):
        first, created = self.refresh()
        self.assertTrue(created)
        with patch('forecasting.weekly.weekly_snapshot', return_value=snapshot()), \
                patch('forecasting.weekly.train_weekly', side_effect=AssertionError('Must reuse')):
            same, created = refresh_weekly()
        self.assertFalse(created); self.assertEqual(same.pk, first.pk)
        s = snapshot()
        corrected = replace(s, revision='corrected', sales=(replace(s.sales[0], revenue=Decimal('999')), *s.sales[1:]))
        second, created = self.refresh(corrected)
        self.assertTrue(created); self.assertNotEqual(first.pk, second.pk)
        first.refresh_from_db();self.assertEqual(first.source_revision, 'weekly-revision')
        self.assertFalse(MonthlyModelRun.objects.exists())
        self.assertFalse(MonthlyIssuedForecast.objects.exists())
        self.assertFalse(MonthlyDeployment.objects.exists())

    def test_new_cutoff_reuses_identical_weekly_fit_without_optimization(self):
        first, _ = self.refresh()
        later = replace(snapshot(), as_of=date(2026, 1, 12))
        with patch('forecasting.weekly.weekly_snapshot', return_value=later), \
                patch('forecasting.weekly.train_weekly', side_effect=AssertionError('Must reuse fitted state')):
            second, created = refresh_weekly()
        self.assertTrue(created);self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.findings['reused_from_model_id'], first.pk)
        self.assertEqual(second.findings['artifact_sha256'], first.findings['artifact_sha256'])

    def test_source_or_cutoff_race_aborts_save(self):
        for changed in (replace(snapshot(), revision='changed'), replace(snapshot(), as_of=date(2026, 1, 12))):
            with patch('forecasting.weekly.weekly_snapshot', side_effect=[snapshot(), changed]), \
                    patch('forecasting.weekly.train_weekly', side_effect=fixture_train):
                with self.assertRaisesRegex(ValueError, 'changed during fitting'):
                    refresh_weekly()
        self.assertFalse(WeeklyModelRun.objects.exists())

    def test_read_rejects_stale_source_corrupt_state_and_invalid_test_rows(self):
        run, _ = self.refresh()
        self.assertEqual(weekly_report(replace(snapshot(), revision='changed'))['status'], 'refresh_required')
        self.assertEqual(weekly_report(snapshot())['status'], 'stale_data')
        for kind in ('artifact', 'fold'):
            saved = deepcopy(run.findings)
            if kind == 'artifact':saved['artifact']['scale'] = 0
            else:saved['candidates'][saved['selected_candidate']]['test_rows'][0]['actual'] = 999
            WeeklyModelRun.objects.filter(pk=run.pk).update(findings=saved)
            self.assertIn(weekly_report(snapshot())['status'], ('invalid_model', 'invalid_comparison'))
            WeeklyModelRun.objects.filter(pk=run.pk).update(findings=run.findings)

    def test_admin_get_is_read_only_and_feeds_the_weekly_line_graph(self):
        self.refresh()
        user = get_user_model().objects.create_user(username='weekly-admin', email='weekly-admin@example.test', role='admin')
        self.client.force_authenticate(user)
        with patch('forecasting.weekly.weekly_snapshot', return_value=snapshot()), \
                patch('forecasting.weekly.train_weekly', side_effect=AssertionError('GET must not fit')):
            response = self.client.get('/api/forecasting/weekly/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['test_rows']), 13)
        self.assertFalse(response.data['is_live'])
        self.assertEqual(response.data['forecast'], [])
        from .chart import chart_report
        with patch('forecasting.chart.weekly_snapshot', return_value=snapshot()):
            chart = chart_report('weekly')
        self.assertEqual(sum(r['predicted'] is not None for r in chart['views']['regular']['history']), 13)
        self.assertEqual(chart['views']['regular']['prediction_basis'], 'weekly_one_step_backtest')
        self.assertEqual(chart['views']['combined']['forecast'], [])

    def test_staff_and_anonymous_cannot_read_weekly_model(self):
        user = get_user_model().objects.create_user(username='weekly-staff', email='weekly-staff@example.test', role='staff')
        for u, status in ((user, 403), (None, 401)):
            self.client.force_authenticate(u)
            self.assertEqual(self.client.get('/api/forecasting/weekly/').status_code, status)

    def test_command_exports_real_evidence_and_worker_refreshes_weekly_offline(self):
        with TemporaryDirectory() as folder, patch('forecasting.weekly.weekly_snapshot', return_value=snapshot()), \
                patch('forecasting.weekly.train_weekly', side_effect=fixture_train):
            call_command('refresh_weekly_forecasts', output=Path(folder), stdout=StringIO())
            self.assertTrue((Path(folder)/'weekly-backtest.csv').exists())
        with patch('forecasting.management.commands.watch_forecast_models.call_command') as command:
            call_command('watch_forecast_models', once=True, stdout=StringIO())
        self.assertIn('run_forecast_refresh', [c.args[0] for c in command.call_args_list])
        with self.assertRaises(CommandError):
            call_command('refresh_weekly_forecasts', as_of=date.max, stdout=StringIO())
