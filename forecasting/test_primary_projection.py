"""Exact primary SARIMA reuse, dependency invalidation and sparse-history policy."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from .data import RecordedSale, SalesSnapshot
from .learning import ENGINE_VERSION, digest, fit_model, month_end, monthly_rows, predict_model, shift_month
from .lifecycle import coverage_revision
from .models import MonthlyModelRun, WeeklyModelRun
from .provisional import build, context, fit, primary_monthly, project, refresh, report


def short_component(rows, period):
    return {'status': 'provisional', 'state': fit(rows, 'local_level', 52 if period == 'weekly' else 12),
            'observed_periods': sum(r['actual'] is not None for r in rows), 'missing_periods': 0,
            'seasonal_terms_enabled': False}


class PrimaryProjectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sales = []
        for n in range(36):
            day = shift_month(date(2023, 1, 1), n).replace(day=15)
            for segment, amount in [('regular', 1000+n*7+(n%12)*20), ('feeding', 300+n*3)]:
                sales.append(RecordedSale(len(sales)+1, day, 'account', 'Customer', segment, Decimal(amount)))
        # The primary engine correctly uses the latest contiguous span, not an earlier fragment.
        sales.append(RecordedSale(1000, date(2022, 3, 15), 'account', 'Customer', 'regular', Decimal(20)))
        cls.snapshot = SalesSnapshot(date(2026, 1, 1), 'actual-test-snapshot', tuple(sales), 0)
        rows = monthly_rows(cls.snapshot)
        state = fit_model('sarima_010_100_12', rows)
        cls.primary_run = MonthlyModelRun.objects.create(engine_version=ENGINE_VERSION,
            source_revision=cls.snapshot.revision, coverage_revision=coverage_revision({}), cutoff=cls.snapshot.as_of,
            trained_through=month_end(date(2025, 12, 1)), status='research_candidate', findings={
                'status': 'research_candidate', 'artifact': state, 'artifact_sha256': digest(state),
                'selected_candidate': state['name'], 'selected_test': {'wape_percent': 10, 'periods': 12}})

    def targets(self):
        return {'monthly': [shift_month(date(2026, 1, 1), n).isoformat() for n in range(12)],
                'weekly': [(date(2026, 1, 5)+timedelta(weeks=n)).isoformat() for n in range(13)],
                'yearly': ['2026-01-01', '2027-01-01']}

    def test_saved_primary_parameters_reproduce_next_month_without_fitting(self):
        with patch('statsmodels.tsa.statespace.sarimax.SARIMAX.fit', side_effect=AssertionError('must not refit')):
            component = primary_monthly(self.snapshot)
            point = project(component['state'], 1)[0]
            expected = predict_model(self.primary_run.findings['artifact'])
        self.assertEqual(component['primary_model_id'], self.primary_run.pk)
        self.assertEqual(component['model_basis'], 'primary_monthly_sarima')
        self.assertTrue(component['seasonal_terms_enabled'])
        self.assertEqual(component['observed_periods'], 36)
        self.assertAlmostEqual(point['predicted'], expected, places=2)
        self.assertIsNone(component['test'])
        self.assertEqual(component['primary_monthly_backtest']['basis'], 'rolling_one_month_ahead')

    def test_monthly_yearly_and_combined_share_the_primary_path_and_keep_metrics_separate(self):
        with patch('forecasting.provisional.train', side_effect=short_component) as training:
            result = build({'monthly': self.snapshot, 'weekly': self.snapshot}, self.targets())
        self.assertEqual(training.call_count, 3)  # Primary monthly is adapted, not independently trained.
        monthly = result['periods']['monthly']
        yearly = result['periods']['yearly']
        expected = project(primary_monthly(self.snapshot)['state'], 24)
        for point, saved in zip(monthly['regular']['points'], expected):
            self.assertEqual(point['predicted'], saved['predicted'])
        for year_index, point in enumerate(yearly['regular']['points']):
            self.assertEqual(point['predicted'], round(sum(p['predicted'] for p in expected[12*year_index:12*(year_index+1)]), 2))
        for period in ('monthly', 'yearly'):
            views = result['periods'][period]
            for regular, feeding, combined in zip(*(views[v]['points'] for v in ('regular', 'feeding', 'combined'))):
                self.assertAlmostEqual(combined['predicted'], regular['predicted']+feeding['predicted'], places=2)
                self.assertFalse(combined['production_approved'])
            self.assertEqual(views['combined']['component_models']['regular']['primary_model_id'], self.primary_run.pk)
            self.assertIsNone(views['regular']['metrics'])
        self.assertIsNone(yearly['regular']['primary_monthly_backtest'])
        self.assertEqual(monthly['regular']['primary_monthly_backtest']['metrics']['wape_percent'], 10)

    def test_eligible_source_change_withholds_monthly_and_yearly_without_silent_fallback(self):
        changed = replace(self.snapshot, revision='corrected-source')
        with patch('forecasting.provisional.train', side_effect=short_component) as training:
            result = build({'monthly': changed, 'weekly': changed}, self.targets())
        self.assertEqual(training.call_count, 3)
        for period in ('monthly', 'yearly'):
            views = result['periods'][period]
            self.assertEqual(views['regular']['status'], 'primary_refresh_required')
            self.assertEqual(views['regular']['points'], [])
            self.assertEqual(views['combined']['points'], [])
            self.assertTrue(views['feeding']['points'])
        self.assertTrue(result['periods']['weekly']['regular']['points'])

    def test_short_or_gapped_data_keeps_explicit_fallback(self):
        short = replace(self.snapshot, revision='short-source', sales=tuple(s for s in self.snapshot.sales if s.day.year == 2025))
        with patch('forecasting.provisional.train', side_effect=short_component) as training:
            result = build({'monthly': short, 'weekly': short}, self.targets())
        self.assertEqual(training.call_count, 4)
        regular = result['periods']['monthly']['regular']
        self.assertEqual(regular['model_basis'], 'provisional_fallback')
        self.assertEqual(regular['fallback_reason'], 'primary_requires_36_contiguous_completed_months')
        self.assertIsNone(regular['primary_model_id'])
        self.assertTrue(regular['points'])

    def test_corrupt_checksum_or_valid_checksum_with_wrong_history_is_withheld(self):
        original = deepcopy(self.primary_run.findings)
        for recompute in (False, True):
            findings = deepcopy(original)
            findings['artifact']['normalized_history'][0] += 1
            if recompute:
                findings['artifact_sha256'] = digest(findings['artifact'])
            self.primary_run.findings = findings
            self.primary_run.save(update_fields=['findings'])
            self.assertEqual(primary_monthly(self.snapshot)['status'], 'invalid_primary_artifact')

    def test_primary_change_invalidates_bundle_get_does_not_fit_and_old_bundle_survives(self):
        with patch('forecasting.provisional.load_snapshot', return_value=self.snapshot), \
                patch('forecasting.provisional.train', side_effect=short_component):
            run, _ = refresh(date(2026, 1, 1))
        run.refresh_from_db()  # Compare persisted JSON lists, not pre-save Python tuples.
        frozen = deepcopy(run.findings)
        with patch('forecasting.provisional.load_snapshot', return_value=self.snapshot), \
                patch('statsmodels.tsa.statespace.sarimax.SARIMAX.fit', side_effect=AssertionError('GET must not fit')):
            before = context(date(2026, 1, 1))[2]
            current = report('monthly', date(2026, 1, 1))
            self.assertEqual(current['views']['regular']['primary_model_id'], self.primary_run.pk)
            self.primary_run.findings['selected_candidate'] = 'sarima_000_100_12'
            self.primary_run.save(update_fields=['findings'])
            self.assertNotEqual(context(date(2026, 1, 1))[2], before)
            self.assertEqual(report('monthly', date(2026, 1, 1))['status'], 'refresh_required')
        run.refresh_from_db()
        self.assertEqual(run.findings, frozen)


class WeeklyPrimaryProjectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_graph_sarima import snapshot, run_for
        cls.snapshot = snapshot()
        retained = run_for(cls.snapshot)
        cls.weekly_run = WeeklyModelRun.objects.create(engine_version='regular-weekly-sarima-v1',
            source_revision=cls.snapshot.revision, cutoff=retained.cutoff,
            trained_through=retained.trained_through, status=retained.status, findings=retained.findings)

    def targets(self):
        return {'weekly': [(date(2026, 11, 2)+timedelta(weeks=n)).isoformat() for n in range(13)],
                'monthly': [shift_month(date(2026, 11, 1), n).isoformat() for n in range(12)],
                'yearly': ['2027-01-01', '2028-01-01']}

    def test_provisional_weekly_uses_the_same_exact_sarima_state_and_leaves_monthly_separate(self):
        from .graph_sarima import regular_weekly_component, regular_weekly_dependency
        from .provisional import future_rows
        expected = regular_weekly_component(regular_weekly_dependency(self.snapshot), allow_training=False)
        with patch('forecasting.provisional.train', return_value={'status': 'insufficient_data'}) as fallback, \
                patch('forecasting.graph_sarima.train_weekly', side_effect=AssertionError('No alternate fit')), \
                patch('statsmodels.tsa.statespace.sarimax.SARIMAX.fit', side_effect=AssertionError('No optimization')):
            result = build({'weekly': self.snapshot, 'monthly': self.snapshot}, self.targets())
        weekly = result['periods']['weekly']['regular']
        self.assertEqual(weekly['points'], future_rows(expected, self.targets()['weekly'], 'weekly'))
        self.assertEqual(len(weekly['points']), 13)
        self.assertEqual(weekly['primary_model_id'], self.weekly_run.pk)
        self.assertEqual(weekly['model_basis'], 'primary_weekly_sarima')
        self.assertIsNone(weekly['metrics'])
        self.assertEqual(weekly['primary_weekly_backtest']['basis'], 'rolling_one_week_ahead')
        self.assertIsNone(weekly['primary_monthly_backtest'])
        self.assertTrue(all(not call.args[0] for call in fallback.call_args_list))
        self.assertEqual(result['periods']['monthly']['regular']['points'], [])
        self.assertEqual(result['periods']['monthly']['regular']['status'], 'primary_refresh_required')

    def test_dependency_mutation_invalidates_get_without_fitting_or_overwriting_older_bundle(self):
        with patch('forecasting.provisional.load_snapshot', return_value=self.snapshot), \
                patch('forecasting.provisional.train', return_value={'status': 'insufficient_data'}):
            bundle, _ = refresh(date(2026, 10, 28))
        bundle.refresh_from_db()
        frozen = deepcopy(bundle.findings)
        with patch('forecasting.provisional.load_snapshot', return_value=self.snapshot), \
                patch('forecasting.graph_sarima.train_weekly', side_effect=AssertionError('GET must not fit')), \
                patch('statsmodels.tsa.statespace.sarimax.SARIMAX.fit', side_effect=AssertionError('GET must not fit')):
            before = context(date(2026, 10, 28))[2]
            current = report('weekly', date(2026, 10, 28))
            self.assertEqual(current['views']['regular']['primary_model_id'], self.weekly_run.pk)
            corrupt = deepcopy(self.weekly_run.findings)
            corrupt['artifact']['params'][-1] = -1
            corrupt['artifact_sha256'] = digest(corrupt['artifact'])
            WeeklyModelRun.objects.filter(pk=self.weekly_run.pk).update(findings=corrupt)
            self.assertNotEqual(context(date(2026, 10, 28))[2], before)
            self.assertEqual(report('weekly', date(2026, 10, 28))['status'], 'refresh_required')
            result = build({'weekly': self.snapshot, 'monthly': self.snapshot}, self.targets(), frozen)
            self.assertEqual(result['periods']['weekly']['regular']['status'], 'invalid_primary_artifact')
            self.assertEqual(result['periods']['weekly']['regular']['points'], [])
        bundle.refresh_from_db()
        self.assertEqual(bundle.findings, frozen)
