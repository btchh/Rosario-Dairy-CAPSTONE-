from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from . import graph_range
from .views import BetaGraphQuery, BetaGraphView
from rest_framework.test import APIRequestFactory, force_authenticate


class GraphRangeTests(SimpleTestCase):
    def run_range(self, start, end, period='monthly', retained_issues=None):
        def point(day, value, past=False):
            return {'date': day, 'end_date': '2026-11-30', 'past_prediction': value if past else None,
                    'future_prediction': None if past else value, 'prediction_basis': 'historical_test' if past else 'forecast',
                    'forecast_origin': '2026-11-01', 'issued_provenance': None}
        data = {'scope': 'real', 'as_of': '2026-11-04', 'readiness': {'production_approved': False},
                'views': {key: {'points': [point('2026-10-01', 10, True), point('2026-11-01', 20)],
                               'readiness': {'production_approved': False}} for key in ('regular', 'feeding', 'combined')},
                'seasonal_outlook': {'views': {key: {'points': [point('2027-04-01', 30)]}
                                             for key in ('regular', 'feeding', 'combined')}}}
        sale = lambda day, segment, value: SimpleNamespace(day=date.fromisoformat(day), segment=segment, revenue=Decimal(value))
        sales = [sale('2025-01-02', 'regular', '0'), sale('2025-03-05', 'regular', '100'),
                 sale('2025-03-06', 'feeding', '50'), sale('2026-11-04', 'regular', '12')]
        with patch.object(graph_range.timezone, 'localdate', return_value=date(2026, 11, 4)), \
                patch.object(graph_range, 'report', return_value=deepcopy(data)), \
                patch.object(graph_range, 'load_snapshot', return_value=SimpleNamespace(sales=sales)), \
                patch.object(graph_range, 'historical_comparisons', return_value={'2025-03-01': 90}), \
                patch.object(graph_range, '_issued_predictions', side_effect=lambda period, target, today: retained_issues[target]), \
                patch.object(graph_range.GraphPrediction.objects, 'filter') as issues:
            issues.return_value.values_list.return_value = list(retained_issues or {})
            return graph_range.range_report(period, 'real', date.fromisoformat(start), date.fromisoformat(end))

    def test_past_range_keeps_full_calendar_zero_missing_and_component_actuals(self):
        result = self.run_range('2025-01-15', '2025-03-10')
        points = result['views']['combined']['points']
        self.assertEqual([p['actual'] for p in points], [0, None, 150])
        self.assertEqual(points[0]['date'], '2025-01-01')
        self.assertEqual(points[-1]['end_date'], '2025-03-31')
        regular = result['views']['regular']
        self.assertEqual(regular['points'][-1]['past_prediction'], 90)
        self.assertEqual(regular['evaluation']['historical_test']['periods'], 1)
        self.assertIsNone(points[-1]['past_prediction'])
        self.assertFalse(regular['readiness']['production_approved'])

    def test_mixed_range_is_not_centered_and_preserves_saved_values(self):
        result = self.run_range('2025-01-01', '2027-04-30')
        points = result['views']['regular']['points']
        self.assertEqual(len(points), 28)
        current = next(p for p in points if p['is_current'])
        self.assertEqual(current['date'], '2026-11-01')
        self.assertEqual(current['actual'], 12)
        self.assertEqual(current['future_prediction'], 20)
        self.assertEqual(points[-1]['future_prediction'], 30)
        self.assertEqual(result['as_of'], '2026-11-04')
        self.assertIsNone(result['seasonal_outlook'])

    def test_future_range_keeps_unavailable_tail_without_projecting(self):
        with patch('forecasting.beta_graph.fit', side_effect=AssertionError('GET must not fit')):
            result = self.run_range('2027-04-01', '2027-08-31')
        points = result['views']['regular']['points']
        self.assertEqual(len(points), 5)
        self.assertEqual([p['future_prediction'] for p in points], [30, None, None, None, None])
        self.assertTrue(all(p['actual'] is None and not p['is_current'] for p in points))
        self.assertEqual(result['readiness']['status'], 'partially_available')

    def test_weekly_cross_year_and_yearly_buckets_remain_whole(self):
        result = self.run_range('2026-12-31', '2027-01-05', 'weekly')
        self.assertEqual(result['range']['start_date'], '2026-12-28')
        self.assertEqual(result['range']['end_date'], '2027-01-10')
        result = self.run_range('2025-04-15', '2027-02-01', 'yearly')
        self.assertEqual(result['range']['start_date'], '2025-01-01')
        self.assertEqual(result['range']['end_date'], '2027-12-31')

    def test_range_validation_accepts_future_and_rejects_ambiguous_or_unbounded_requests(self):
        good = {'start_date': '2025-01-01', 'end_date': '2027-01-01'}
        query = BetaGraphQuery(data=good)
        self.assertTrue(query.is_valid(), query.errors)
        self.assertNotIn('as_of', query.validated_data)
        for bad in [{'start_date': '2025-01-01'}, {**good, 'as_of': '2025-01-01'},
                    {**good, 'end_date': '2024-01-01'}, {**good, 'end_date': '2026-02-30'},
                    {**good, 'scope': 'demo2026'}, {**good, 'start_date': '0001-01-01'},
                    {**good, 'period': 'weekly', 'end_date': '2099-01-01'}]:
            self.assertFalse(BetaGraphQuery(data=bad).is_valid(), bad)

    def test_existing_historical_origin_query_remains_supported(self):
        query = BetaGraphQuery(data={'as_of': '2025-06-30'})
        self.assertTrue(query.is_valid(), query.errors)
        self.assertEqual(query.validated_data['as_of'], date(2025, 6, 30))

    def test_endpoint_dispatches_target_dates_without_passing_an_as_of(self):
        request = APIRequestFactory().get('/api/forecasting/graph/',
            {'period': 'weekly', 'start_date': '2025-01-01', 'end_date': '2027-01-01'})
        force_authenticate(request, user=SimpleNamespace(is_authenticated=True, role='admin'))
        with patch.object(BetaGraphView, 'get_throttles', return_value=[]), \
                patch('forecasting.views.range_report', return_value={'requested_range': {}}) as target, \
                patch('forecasting.views.beta_graph_report', side_effect=AssertionError('Wrong date semantics')):
            response = BetaGraphView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        target.assert_called_once_with(period='weekly', scope='real',
            start_date=date(2025, 1, 1), end_date=date(2027, 1, 1))

    def test_retained_monthly_comparisons_are_withheld_if_source_changed(self):
        snapshot = SimpleNamespace(revision='current', sales=[])
        run = SimpleNamespace(source_revision='old', status='research_candidate')
        with patch.object(graph_range, 'model_context', return_value=(snapshot, None, run, None, None, None)):
            self.assertEqual(graph_range.historical_comparisons('monthly'), {})

    def test_original_issues_take_precedence_without_reconstructing_combined(self):
        provenance = {'forecast_origin': '2025-02-01', 'horizon': 1, 'run_id': 17}
        issues = {date(2025, 3, 1): {'regular': {'value': 0, 'provenance': provenance}, 'combined': None}}
        result = self.run_range('2025-03-01', '2025-03-31', retained_issues=issues)
        regular = result['views']['regular']['points'][0]
        self.assertEqual(regular['past_prediction'], 0)
        self.assertEqual(regular['prediction_basis'], 'issued_forecast')
        self.assertEqual(regular['issued_provenance'], provenance)
        self.assertIsNone(regular['model_details'])
        self.assertIsNone(result['views']['combined']['points'][0]['past_prediction'])
