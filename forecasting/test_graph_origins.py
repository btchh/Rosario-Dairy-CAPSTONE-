"""One latest outlook, immutable issues, and explicit current-year conditioning."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from sales.models import Transaction
from . import beta_graph as graph
from .data import RecordedSale, SalesSnapshot
from .models import GraphForecastRun, GraphPrediction


def monthly_path(component, targets, period):
    return [{'date': target, 'predicted': component['state']['level']} for target in targets]


class AnnualOutlookTests(SimpleTestCase):
    def setUp(self):
        self.target = date(2026, 1, 1)
        self.cutoff = date(2026, 10, 1)
        self.rows = [{'date': f'2026-{month:02d}-01', 'actual': 100.0} for month in range(1, 10)]
        self.component = {'state': {'level': 200.0}}

    def test_actual_closed_months_plus_remaining_path_and_disclosed_current_floor(self):
        original = deepcopy((self.rows, self.component))
        path = [{'date': '2026-10-01', 'predicted': 200.0},
                {'date': '2026-11-01', 'predicted': 300.0},
                {'date': '2026-12-01', 'predicted': 400.0}]
        with patch('forecasting.beta_graph.future_rows', return_value=path) as project:
            result = graph.annual_outlook(self.component, self.target, self.cutoff, self.rows, 250.0)
        self.assertEqual(result['prediction'], 1850.0)
        self.assertEqual((result['observed_revenue'], result['forecast_revenue']), (900.0, 950.0))
        self.assertEqual((result['completed_months'], result['forecast_months']), (9, 3))
        self.assertEqual(result['current_month_model_prediction'], 200.0)
        self.assertEqual(result['current_month_estimate'], 250.0)
        self.assertTrue(result['current_month_floor_applied'])
        project.assert_called_once_with(self.component, ['2026-10-01', '2026-11-01', '2026-12-01'], 'monthly')
        self.assertEqual((self.rows, self.component), original)

    def test_small_current_actual_does_not_prorate_or_reduce_the_model_path(self):
        with patch('forecasting.beta_graph.future_rows', side_effect=monthly_path):
            result = graph.annual_outlook(self.component, self.target, self.cutoff, self.rows, 80.0)
        self.assertEqual(result['prediction'], 1500.0)
        self.assertEqual(result['current_month_estimate'], 200.0)
        self.assertFalse(result['current_month_floor_applied'])

    def test_unknown_closed_month_withholds_annual_sum_instead_of_zero_filling(self):
        self.rows[3]['actual'] = None
        with patch('forecasting.beta_graph.future_rows', side_effect=AssertionError('Unknown total')):
            result = graph.annual_outlook(self.component, self.target, self.cutoff, self.rows)
        self.assertIsNone(result['prediction'])
        self.assertEqual(result['missing_completed_months'], ['2026-04-01'])

    def test_absent_leading_months_are_unknown_even_with_a_fitted_model(self):
        with patch('forecasting.beta_graph.future_rows', side_effect=AssertionError('Incomplete year')):
            result = graph.annual_outlook(self.component, self.target, self.cutoff, self.rows[6:])
        self.assertIsNone(result['prediction'])
        self.assertEqual(len(result['missing_completed_months']), 6)

    def test_confirmed_zero_month_counts_as_a_known_actual(self):
        self.rows[3]['actual'] = 0.0
        with patch('forecasting.beta_graph.future_rows', side_effect=monthly_path):
            result = graph.annual_outlook(self.component, self.target, self.cutoff, self.rows, 0.0)
        self.assertEqual(result['prediction'], 1400.0)
        self.assertEqual(result['missing_completed_months'], [])

    def test_january_and_december_do_not_overlap_observed_and_forecast_months(self):
        with patch('forecasting.beta_graph.future_rows', side_effect=monthly_path):
            january = graph.annual_outlook(self.component, self.target, self.target, [], 250.0)
            rows = [{'date': f'2026-{month:02d}-01', 'actual': 100.0} for month in range(1, 12)]
            december = graph.annual_outlook(self.component, self.target, date(2026, 12, 1), rows, 250.0)
        self.assertEqual((january['completed_months'], january['forecast_months'], january['prediction']), (0, 12, 2450.0))
        self.assertEqual((december['completed_months'], december['forecast_months'], december['prediction']), (11, 1, 1350.0))

    def test_invalid_or_unavailable_saved_path_does_not_invent_an_annual_value(self):
        targets = ['2026-10-01', '2026-11-01', '2026-12-01']
        for points in ([], [{'date': target, 'predicted': float('inf')} for target in targets],
                       [{'date': target, 'predicted': -1} for target in targets],
                       [{'date': '2027-01-01', 'predicted': 100}]*3):
            with self.subTest(points=points), patch('forecasting.beta_graph.future_rows', return_value=points):
                self.assertIsNone(graph.annual_outlook(self.component, self.target, self.cutoff, self.rows)['prediction'])
        self.assertIsNone(graph.annual_outlook({}, self.target, self.cutoff, self.rows)['prediction'])

    def test_nonfinite_actuals_withhold_the_estimate_and_are_safe_to_serialize(self):
        import json
        for value in (float('inf'), float('nan'), -1, 'invalid'):
            with self.subTest(value=value):
                rows = deepcopy(self.rows)
                rows[0]['actual'] = value
                result = graph.annual_outlook(self.component, self.target, self.cutoff, rows)
                self.assertIsNone(result['prediction'])
                self.assertEqual(result['invalid_completed_months'], ['2026-01-01'])
                json.dumps(result, allow_nan=False)
                current = graph.annual_outlook(self.component, self.target, self.cutoff, self.rows, value)
                self.assertIsNone(current['prediction'])
                self.assertTrue(current['invalid_current_month_actual'])
                json.dumps(current, allow_nan=False)


class LatestOriginContextTests(SimpleTestCase):
    def snapshot(self, cutoff):
        sales = tuple(RecordedSale(i, date(2026, month, 10), 'c', 'Customer', segment, Decimal('100'))
                      for i, (month, segment) in enumerate(
                          ((m, s) for m in range(1, 11) for s in ('regular', 'feeding')), 1)
                      if date(2026, month, 10) < cutoff)
        return SalesSnapshot(cutoff, f'source-{cutoff}', sales, 0)

    def test_current_and_future_years_share_the_latest_completed_month_cutoff(self):
        with patch('forecasting.beta_graph.load_snapshots',
                   side_effect=lambda cutoffs: {day: self.snapshot(day) for day in cutoffs}), \
                patch('forecasting.beta_graph._primary', return_value=None), \
                patch('forecasting.beta_graph.valid_coverage', return_value={}):
            ctx = graph.context('yearly', 'real', date(2026, 10, 28))
        for segment in ('regular', 'feeding'):
            items = [ctx['inputs'][(date(year, 1, 1), segment)] for year in (2026, 2027, 2028)]
            self.assertEqual({item['cutoff'] for item in items}, {date(2026, 10, 1)})
            self.assertEqual(len({item['signature'] for item in items}), 1)
            self.assertTrue(all(row['date'] < '2026-10-01' for item in items for row in item['rows']))
        self.assertEqual(ctx['inputs'][(date(2025, 1, 1), 'regular')]['cutoff'], date(2025, 1, 1))

    def test_demo_current_and_future_years_keep_the_fixed_pre_demo_origin(self):
        with patch('forecasting.beta_graph.load_snapshots',
                   side_effect=lambda cutoffs: {day: self.snapshot(day) for day in cutoffs}), \
                patch('forecasting.beta_graph._primary', return_value=None), \
                patch('forecasting.beta_graph.valid_coverage', return_value={}), \
                patch('forecasting.beta_graph.Transaction.objects.filter') as transactions:
            transactions.return_value.order_by.return_value.values.return_value = []
            ctx = graph.context('yearly', 'demo2026', date(2026, 10, 28))
        self.assertEqual({ctx['inputs'][(date(year, 1, 1), 'regular')]['cutoff']
                          for year in (2026, 2027, 2028)}, {date(2026, 1, 1)})


class GraphOriginReplayTests(TestCase):
    def setUp(self):
        self.assertTrue(str(self.connection_name()).startswith('test_'), 'Disposable test database required')
        self.admin = get_user_model().objects.create_user(username='origin-admin', role='admin')
        for year, last_month in ((2025, 12), (2026, 9)):
            for month in range(1, last_month+1):
                self.sale(date(year, month, 15), 'regular', 100)
                self.sale(date(year, month, 16), 'feeding', 50)

    @staticmethod
    def connection_name():
        from django.db import connection
        return connection.settings_dict['NAME']

    def sale(self, day, segment, amount):
        sale = Transaction.objects.create(handled_by=self.admin, total_amount=amount,
                                          source_sales_segment=segment)
        Transaction.objects.filter(pk=sale.pk).update(created_at=timezone.make_aware(
            datetime.combine(day, datetime.min.time()))+timedelta(hours=12))
        return sale

    @staticmethod
    def clock(day):
        return timezone.make_aware(datetime.combine(day, datetime.min.time()))+timedelta(hours=18)

    @staticmethod
    def train(rows, unit):
        return {'status': 'provisional', 'state': {'level': rows[-1]['actual'],
                                                  'trained_through': rows[-1]['date']}} if rows else {}

    def refresh(self, period, day, point):
        with patch('django.utils.timezone.now', return_value=self.clock(day)), \
                patch('forecasting.beta_graph._train', side_effect=self.train), \
                patch('forecasting.beta_graph._point', return_value=point), \
                patch('forecasting.beta_graph.future_rows', side_effect=monthly_path):
            return graph.refresh(period, 'real', day)[0]

    @staticmethod
    def point(payload, view, target):
        return next(point for point in payload['views'][view]['points'] if point['date'] == target)

    def test_current_outlook_uses_new_fit_and_retains_old_issue_separately(self):
        old = self.refresh('weekly', date(2026, 10, 25), 100.0)
        GraphForecastRun.objects.filter(pk=old.pk).update(engine_version='centered-beta-sarima-v4')
        frozen = dict(GraphPrediction.objects.values_list('pk', 'value'))
        self.sale(date(2026, 10, 23), 'regular', 900)
        latest = self.refresh('weekly', date(2026, 10, 28), 1000.0)
        current = self.point(latest.findings, 'combined', '2026-10-26')
        self.assertEqual(current['future_prediction'], 2000.0)
        self.assertEqual(current['issued_prediction'], 200.0)
        self.assertEqual(current['prediction_basis'], 'forecast')
        self.assertEqual(current['forecast_origin'], '2026-10-26')
        self.assertEqual(current['horizon'], 1)
        self.assertEqual(current['issued_provenance']['run_id'], old.pk)
        self.assertEqual(current['issued_provenance']['engine_version'], 'centered-beta-sarima-v4')
        self.assertEqual(current['issued_provenance']['forecast_origin'], '2026-10-19')
        self.assertEqual(current['issued_provenance']['horizon'], 2)
        self.assertEqual(current['issued_provenance']['source_revision'], old.source_revision)
        self.assertEqual(dict(GraphPrediction.objects.filter(pk__in=frozen).values_list('pk', 'value')), frozen)
        future = [p for p in latest.findings['views']['combined']['points'] if p['future_prediction'] is not None]
        self.assertEqual({p['forecast_origin'] for p in future}, {'2026-10-26'})
        self.assertEqual([p['horizon'] for p in future], [1, 2, 3, 4])

    def test_completed_target_replays_issued_value_instead_of_reconstructed_value(self):
        old = self.refresh('weekly', date(2026, 10, 15), 100.0)
        latest = self.refresh('weekly', date(2026, 10, 28), 1000.0)
        past = self.point(latest.findings, 'combined', '2026-10-19')
        self.assertEqual(past['past_prediction'], 200.0)
        self.assertEqual(past['issued_prediction'], 200.0)
        self.assertEqual(past['prediction_basis'], 'issued_forecast')
        self.assertEqual(past['issued_provenance']['run_id'], old.pk)

    def test_partial_current_issue_does_not_replace_or_remove_fresh_combined_outlook(self):
        old = GraphForecastRun.objects.create(engine_version='old', scope='real', period='weekly',
                                              as_of=date(2026, 10, 25), source_revision='old', findings={})
        with patch('django.utils.timezone.now', return_value=self.clock(date(2026, 10, 25))):
            issue = GraphPrediction.objects.create(run=old, view='regular', target=date(2026, 10, 26), value=999)
        latest = self.refresh('weekly', date(2026, 10, 28), 1000.0)
        regular = self.point(latest.findings, 'regular', '2026-10-26')
        combined = self.point(latest.findings, 'combined', '2026-10-26')
        self.assertEqual(regular['issued_provenance']['prediction_id'], issue.pk)
        self.assertEqual(regular['issued_prediction'], 999)
        self.assertEqual(regular['future_prediction'], 1000)
        self.assertIsNone(combined['issued_prediction'])
        self.assertEqual(combined['future_prediction'], 2000)

    def test_historical_extraction_does_not_disclose_issues_saved_after_extraction(self):
        run = GraphForecastRun.objects.create(engine_version='old', scope='real', period='weekly',
                                              as_of=date(2026, 10, 30), source_revision='later', findings={})
        with patch('django.utils.timezone.now', return_value=self.clock(date(2026, 10, 30))):
            GraphPrediction.objects.create(run=run, view='regular', target=date(2026, 11, 2), value=999)
        self.assertEqual(graph._issued_predictions('weekly', date(2026, 11, 2), date(2026, 10, 28)), {})
        self.assertEqual(graph._issued_predictions('weekly', date(2026, 11, 2), date(2026, 10, 30))['regular']['value'], 999)

    def test_get_preserves_saved_weekly_predictions_without_fitting_or_reprojection(self):
        day = date(2026, 10, 28)
        run = self.refresh('weekly', day, 1000.0)
        with patch('django.utils.timezone.now', return_value=self.clock(day)), \
                patch('forecasting.beta_graph._train', side_effect=AssertionError('GET must not fit')), \
                patch('forecasting.beta_graph._point', side_effect=AssertionError('GET uses saved weekly values')):
            payload = graph.report('weekly', 'real', day)
        self.assertEqual([p['future_prediction'] for p in payload['views']['combined']['points']],
                         [p['future_prediction'] for p in run.findings['views']['combined']['points']])

    def test_annual_get_conditions_only_current_year_without_mutating_models_or_issues(self):
        day = date(2026, 10, 28)
        self.sale(date(2026, 10, 20), 'regular', 250)
        run = self.refresh('yearly', day, 1000.0)
        frozen_findings = deepcopy(run.findings)
        frozen_issues = list(GraphPrediction.objects.values('pk', 'value'))
        self.assertEqual(self.point(run.findings, 'regular', '2026-01-01')['future_prediction'], 1350.0)
        self.sale(date(2026, 10, 27), 'regular', 400)
        with patch('django.utils.timezone.now', return_value=self.clock(day)), \
                patch('forecasting.beta_graph._train', side_effect=AssertionError('GET must not fit')), \
                patch('forecasting.beta_graph.fit', side_effect=AssertionError('GET must not optimize')), \
                patch('forecasting.beta_graph.future_rows', side_effect=monthly_path):
            payload = graph.report('yearly', 'real', day)
        current = self.point(payload, 'regular', '2026-01-01')
        self.assertEqual(current['future_prediction'], 1750.0)
        self.assertTrue(current['is_nowcast'])
        self.assertEqual(current['forecast_origin'], '2026-10-01')
        self.assertEqual(current['annual_outlook']['current_month_actual'], 650.0)
        self.assertEqual(current['annual_outlook']['current_month_model_prediction'], 100.0)
        self.assertEqual(current['annual_outlook']['observed_revenue'], 900.0)
        self.assertEqual(current['outlook_basis'], 'completed_month_actuals_plus_remaining_monthly_forecast')
        for year in (2027, 2028):
            self.assertEqual(self.point(payload, 'combined', f'{year}-01-01')['future_prediction'],
                             self.point(run.findings, 'combined', f'{year}-01-01')['future_prediction'])
        combined = self.point(payload, 'combined', '2026-01-01')
        self.assertEqual(combined['future_prediction'], current['future_prediction']+
                         self.point(payload, 'feeding', '2026-01-01')['future_prediction'])
        run.refresh_from_db()
        self.assertEqual(run.findings, frozen_findings)
        self.assertEqual(list(GraphPrediction.objects.values('pk', 'value')), frozen_issues)

    def test_unknown_completed_month_withholds_current_annual_only(self):
        Transaction.objects.filter(source_sales_segment='regular', created_at__year=2026,
                                   created_at__month=6).delete()
        run = self.refresh('yearly', date(2026, 10, 28), 1000.0)
        current = self.point(run.findings, 'regular', '2026-01-01')
        self.assertIsNone(current['future_prediction'])
        self.assertEqual(current['annual_outlook']['missing_completed_months'], ['2026-06-01'])
        self.assertIsNone(self.point(run.findings, 'combined', '2026-01-01')['future_prediction'])
        self.assertIsNotNone(self.point(run.findings, 'feeding', '2026-01-01')['future_prediction'])
        self.assertEqual(self.point(run.findings, 'combined', '2027-01-01')['future_prediction'], 2000.0)
