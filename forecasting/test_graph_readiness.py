from copy import deepcopy
from datetime import date

from django.test import SimpleTestCase

from .graph_readiness import describe_graph


class GraphReadinessTests(SimpleTestCase):
    def test_nonfinite_or_non_mapping_saved_graph_is_rejected_without_crashing(self):
        from .beta_graph import checked
        for findings in (None, [], 'bad graph', {'checksum': 'original', 'value': float('inf')},
                         {'checksum': 'original', 'value': float('nan')}):
            with self.subTest(findings_type=type(findings).__name__):
                self.assertIsNone(checked(findings))

    def example(self, period='monthly'):
        day = date(2026, 10, 1)
        state = {'name': 'ar1', 'specification': {'order': [1, 0, 0], 'seasonal_order': [0, 0, 0, 0]},
                 'values': [1, None, 2], 'training_start': '2026-07-01', 'trained_through': '2026-09-01'}
        artifact = {'state': state, 'validation': {'ar1': {'wape': 20, 'sample_count': 1}},
                    'test': {'wape': 25, 'sample_count': 1}}
        ctx = {'unit': 'monthly', 'inputs': {(day, s): {'signature': s, 'cutoff': day, 'primary': None}
                                            for s in ('regular', 'feeding')}}
        point = {'date': day.isoformat(), 'actual': 100, 'past_prediction': None,
                 'future_prediction': 120, 'is_current': True, 'prediction_basis': 'forecast'}
        data = {'status': 'ready', 'period': period, 'views': {s: {'points': [deepcopy(point)], 'evaluation': {}}
                                                            for s in ('regular', 'feeding', 'combined')}}
        return data, ctx, {s: deepcopy(artifact) for s in ('regular', 'feeding')}

    def test_saved_graph_availability_does_not_claim_production_approval(self):
        data, ctx, artifacts = self.example()
        original = deepcopy((data, ctx, artifacts))
        result = describe_graph(data, ctx, artifacts)
        self.assertEqual(result['status'], 'ready')  # Existing API contract retained.
        self.assertEqual(result['readiness']['status'], 'available')
        self.assertFalse(result['readiness']['production_approved'])
        self.assertEqual(result['readiness']['publication_status'], 'research_only')
        details = result['views']['feeding']['model_details']
        self.assertEqual(details['name'], 'ar1')
        self.assertEqual(details['family'], 'ARIMA')
        self.assertFalse(details['seasonal_terms_enabled'])
        self.assertEqual((details['observed_periods'], details['missing_periods']), (2, 1))
        self.assertEqual(details['test']['wape_percent'], 25)
        self.assertEqual(result['views']['feeding']['points'][0]['future_prediction'], 120)
        self.assertEqual((data, ctx, artifacts), original)

    def test_saved_graph_with_no_predictions_is_unavailable(self):
        data, ctx, artifacts = self.example()
        for view in data['views'].values():
            view['points'][0]['future_prediction'] = None
        result = describe_graph(data, ctx, artifacts)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['readiness']['status'], 'unavailable')
        self.assertTrue(all(v['readiness']['status'] == 'unavailable' for v in result['views'].values()))
        self.assertIsNone(result['views']['feeding']['model_details'])

    def test_issued_values_are_not_given_reconstructed_model_metadata(self):
        data, ctx, artifacts = self.example()
        data['views']['regular']['points'][0]['prediction_basis'] = 'issued_forecast'
        result = describe_graph(data, ctx, artifacts)
        point = result['views']['regular']['points'][0]
        self.assertEqual(point['future_prediction'], 120)
        self.assertEqual(point['prediction_basis'], 'issued_forecast')
        self.assertIsNone(point['model_details'])

    def test_yearly_details_keep_monthly_evaluation_and_distinct_origins(self):
        data, ctx, artifacts = self.example('yearly')
        first = date(2026, 1, 1)
        second = date(2027, 1, 1)
        for view in data['views'].values():
            view['points'][0]['date'] = first.isoformat()
            view['points'].append({**view['points'][0], 'date': second.isoformat(), 'is_current': False})
        for segment in ('regular', 'feeding'):
            item = ctx['inputs'].pop((date(2026, 10, 1), segment))
            ctx['inputs'][(first, segment)] = {**item, 'cutoff': first}
            ctx['inputs'][(second, segment)] = {**item, 'cutoff': date(2026, 10, 1)}
        result = describe_graph(data, ctx, artifacts)
        points = result['views']['feeding']['points']
        self.assertEqual([p['model_details']['forecast_origin'] for p in points], ['2026-01-01', '2026-10-01'])
        self.assertTrue(all(p['model_details']['evaluation_period'] == 'monthly' for p in points))
        self.assertEqual(result['views']['combined']['model_details']['name'], 'sum_of_component_predictions')
