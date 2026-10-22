from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from config.test_support import APIClient
from .chart import chart_report, period_start
from .learning import shift_month
from .data import RecordedSale, SalesSnapshot
from .models import MonthlyIssuedForecast, MonthlyModelRun


def context(status='stale_data'):
    def sale(pk, month, segment, amount):
        return RecordedSale(pk, date(2025, month, 15), 'customer', 'Customer', segment, Decimal(amount))
    snapshot = SalesSnapshot(date(2025, 4, 1), 'current', (
        sale(1, 1, 'regular', '100'), sale(2, 2, 'regular', '200'),
        sale(3, 1, 'feeding', '50'), sale(4, 3, 'feeding', '30'),
        sale(5, 1, 'unclassified', '99999'),
    ), 8)
    model = SimpleNamespace(source_revision='current', status='research_candidate', trained_through=date(2025, 3, 31),
        findings={'selected_candidate': 'sarima_010_100_12', 'next_month': {'sample_count': 2},
            'candidates': {'sarima_010_100_12': {'test_rows': [
                {'month': '2025-01-01', 'trained_through': '2024-12-01', 'actual': 100, 'predicted': 90},
                {'month': '2025-02-01', 'trained_through': '2025-01-01', 'actual': 200, 'predicted': 180},
            ]}}})
    issued = SimpleNamespace(target_month=date(2025, 4, 1), value=Decimal('220'), lower_bound=Decimal('200'),
                             upper_bound=Decimal('240')) if status == 'ready' else None
    return snapshot, model, model, {}, issued, status


class ForecastChartDataTests(SimpleTestCase):
    def render(self, ctx=None, period='monthly'):
        with patch('forecasting.chart.model_context', return_value=ctx or context()):
            return chart_report(period=period)

    def test_all_periods_preserve_recorded_totals_and_three_view_calendar_alignment(self):
        for period in ('weekly', 'monthly', 'yearly'):
            with self.subTest(period=period):
                result = self.render(period=period)
                self.assertEqual(result['available_periods'], ['weekly', 'monthly', 'yearly'])
                self.assertEqual(result['period'], period)
                expected = {'regular': 300, 'feeding': 80, 'combined': 380}
                dates = [r['date'] for r in result['views']['combined']['history']]
                for key, total in expected.items():
                    history = result['views'][key]['history']
                    self.assertEqual(sum(r['actual'] or 0 for r in history), total)
                    self.assertEqual([r['date'] for r in history], dates)

    def test_weekly_boundaries_follow_monday_and_preserve_empty_week_gaps(self):
        self.assertEqual(period_start(date(2025, 1, 1), 'weekly'), date(2024, 12, 30))
        self.assertEqual(period_start(date(2024, 2, 29), 'weekly'), date(2024, 2, 26))
        result = self.render(period='weekly')
        regular = result['views']['regular']
        self.assertEqual(regular['history'][0]['date'], '2025-01-13')
        self.assertEqual(regular['history'][0]['end_date'], '2025-01-19')
        self.assertTrue(any(r['actual'] is None for r in regular['history']))
        self.assertTrue(all(r['predicted'] is None for r in regular['history']))
        self.assertIsNone(regular['metrics'])
        self.assertEqual(regular['historical_prediction_status'], 'weekly_model_not_available')

    def test_yearly_aggregates_require_twelve_valid_monthly_predictions(self):
        sales = tuple(RecordedSale(i, date(2025, i, 15), 'customer', 'Customer', 'regular', Decimal('100'))
                      for i in range(1, 13))
        snapshot = SalesSnapshot(date(2026, 1, 1), 'current', sales, 0)
        _, model, _, gate, issued, status = context()
        folds = [{'month': date(2025, i, 1).isoformat(),
                  'trained_through': shift_month(date(2025, i, 1), -1).isoformat(),
                  'actual': 100, 'predicted': 90} for i in range(1, 13)]
        model.findings['candidates']['sarima_010_100_12']['test_rows'] = folds
        ctx = snapshot, model, model, gate, issued, status
        regular = self.render(ctx, period='yearly')['views']['regular']
        self.assertEqual(regular['history'][0]['actual'], 1200)
        self.assertEqual(regular['history'][0]['predicted'], 1080)
        self.assertIsNone(regular['metrics'])
        self.assertEqual(regular['prediction_basis'], 'sum_of_12_monthly_rolling_predictions')
        folds.pop()
        self.assertIsNone(self.render(ctx, period='yearly')['views']['regular']['history'][0]['predicted'])

    def test_monthly_issued_forecast_never_becomes_weekly_or_yearly_approved_point(self):
        for period in ('weekly', 'yearly'):
            regular = self.render(context('ready'), period=period)['views']['regular']
            self.assertEqual(regular['forecast'], [])
            self.assertEqual(regular['status'], 'horizon_not_validated')

    def test_partial_year_is_clipped_to_actual_snapshot_cutoff(self):
        result = self.render(period='yearly')
        row = result['views']['regular']['history'][0]
        self.assertEqual(row['end_date'], '2025-03-31')
        self.assertTrue(row['is_partial_period'])
        self.assertIsNone(row['predicted'])

    def test_three_views_share_calendar_and_combined_totals_exclude_unclassified(self):
        report = self.render()
        self.assertEqual(report['available_views'], ['combined', 'regular', 'feeding'])
        self.assertEqual(report['default_view'], 'combined')
        views = report['views']
        self.assertEqual([r['actual'] for r in views['combined']['history']], [150, 200, 30])
        dates = [r['date'] for r in views['combined']['history']]
        for key in ('regular', 'feeding'):
            self.assertEqual([r['date'] for r in views[key]['history']], dates)
        self.assertEqual(views['combined']['history'][0]['invoice_count'], 2)

    def test_no_invoice_periods_remain_gaps_and_missing_predictions_are_not_zero(self):
        views = self.render()['views']
        self.assertIsNone(views['feeding']['history'][1]['actual'])
        self.assertEqual(views['feeding']['history'][1]['record_status'], 'no_invoice_recorded')
        for key in ('combined', 'feeding'):
            self.assertTrue(all(r['predicted'] is None for r in views[key]['history']))
            self.assertIsNone(views[key]['metrics'])
            self.assertEqual(views[key]['forecast'], [])

    def test_retrospective_line_can_display_when_live_forecast_is_stale(self):
        result = self.render()
        regular = result['views']['regular']
        self.assertEqual(regular['status'], 'stale_data')
        self.assertEqual(regular['forecast'], [])
        self.assertEqual(regular['historical_prediction_status'], 'available')
        self.assertEqual([r['predicted'] for r in regular['history']], [90, 180, None])
        self.assertEqual(regular['metrics']['wape_percent'], 10)
        self.assertEqual(result['default_range'], {'start_date': '2025-01-01', 'end_date': '2025-02-28'})

    def test_source_corrections_withhold_old_predictions_and_scores(self):
        ctx = context();ctx[1].source_revision = 'old'
        regular = self.render(ctx)['views']['regular']
        self.assertEqual(regular['historical_prediction_status'], 'unavailable_or_source_changed')
        self.assertTrue(all(r['predicted'] is None for r in regular['history']))
        self.assertIsNone(regular['metrics'])

    def test_inconsistent_origins_actuals_duplicates_and_invalid_predictions_are_withheld(self):
        for change in ({'trained_through': '2025-01-01'}, {'actual': 999},
                       {'predicted': float('nan')}, {'predicted': -1}, {'month': 'invalid'}):
            with self.subTest(change=change):
                ctx = context()
                folds = ctx[1].findings['candidates']['sarima_010_100_12']['test_rows']
                folds[0].update(change)
                self.assertEqual(self.render(ctx)['views']['regular']['historical_prediction_status'],
                                 'inconsistent_saved_comparison')
        ctx = context()
        folds = ctx[1].findings['candidates']['sarima_010_100_12']['test_rows']
        folds.append(deepcopy(folds[0]))
        self.assertIsNone(self.render(ctx)['views']['regular']['metrics'])

    def test_approved_regular_point_does_not_make_combined_or_feeding_ready(self):
        views = self.render(context('ready'))['views']
        self.assertEqual(views['regular']['forecast'][0]['predicted_revenue'], Decimal('220'))
        self.assertEqual(views['combined']['status'], 'component_unavailable')
        self.assertEqual(views['combined']['forecast'], [])
        self.assertEqual(views['feeding']['status'], 'not_modelled')

    def test_empty_actual_history_has_no_fabricated_rows(self):
        snapshot = SalesSnapshot(date(2025, 4, 1), 'empty', (), 0)
        result = self.render((snapshot, None, None, None, None, 'training_required'))
        self.assertIsNone(result['default_range'])
        self.assertTrue(all(v['history'] == [] for v in result['views'].values()))


class ForecastChartEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_admin_get_reads_three_views_without_fitting_or_issuing(self):
        admin = get_user_model().objects.create_user(username='chart-admin', email='chart-admin@example.test', role='admin')
        self.client.force_authenticate(admin)
        with patch('forecasting.chart.model_context', return_value=context()), \
                patch('forecasting.learning.train', side_effect=AssertionError('GET must not fit')):
            response = self.client.get('/api/forecasting/chart/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data['views']), {'regular', 'feeding', 'combined'})
        self.assertEqual(response.data['scope'], 'real')
        self.assertFalse(MonthlyModelRun.objects.exists())
        self.assertFalse(MonthlyIssuedForecast.objects.exists())

    def test_staff_and_anonymous_cannot_access(self):
        staff = get_user_model().objects.create_user(username='chart-staff', email='chart-staff@example.test', role='staff')
        for user, status in ((staff, 403), (None, 401)):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get('/api/forecasting/chart/').status_code, status)

    def test_unsupported_period_and_synthetic_scope_are_rejected(self):
        admin = get_user_model().objects.create_user(username='chart-query-admin', email='chart-query-admin@example.test', role='admin')
        self.client.force_authenticate(admin)
        with patch('forecasting.views.chart_report', side_effect=AssertionError('Invalid queries must not read results')):
            for query in ('period=daily', 'scope=demo2026'):
                self.assertEqual(self.client.get('/api/forecasting/chart/?'+query).status_code, 400)

    def test_weekly_monthly_and_yearly_queries_select_the_requested_graph_period(self):
        admin = get_user_model().objects.create_user(username='chart-period-admin', email='chart-period-admin@example.test', role='admin')
        self.client.force_authenticate(admin)
        with patch('forecasting.chart.model_context', return_value=context()):
            for period in ('weekly', 'monthly', 'yearly'):
                response = self.client.get('/api/forecasting/chart/?period='+period)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['period'], period)
                self.assertEqual(len(response.data['views']), 3)
