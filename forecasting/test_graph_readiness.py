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

    def test_monthly_model_steps_disclose_missing_history_without_changing_legacy_horizon(self):
        data, ctx, artifacts = self.example()
        current, future = date(2026, 10, 1), date(2026, 11, 1)
        for view in data['views'].values():
            view['points'][0]['horizon'] = 1
            view['points'].append({**view['points'][0], 'date': future.isoformat(),
                                   'horizon': 2, 'is_current': False})
        for segment, artifact in artifacts.items():
            artifact['state']['trained_through'] = '2025-12-01'
            ctx['inputs'][(future, segment)] = ctx['inputs'][(current, segment)]
        original = deepcopy((data, ctx, artifacts))
        result = describe_graph(data, ctx, artifacts)
        points = result['views']['regular']['points']
        self.assertEqual([p['horizon'] for p in points], [1, 2])
        self.assertEqual([p['model_details']['projection_steps'] for p in points], [10, 11])
        self.assertEqual([p['model_details']['missing_observation_periods_before_current'] for p in points], [9, 9])
        freshness = result['readiness']['source_freshness']
        self.assertEqual(freshness['status'], 'stale_data')
        self.assertEqual(freshness['components']['regular']['expected_latest_closed_period'], '2026-09-01')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual((data, ctx, artifacts), original)

    def test_weekly_steps_use_bucket_start_and_future_weeks_are_not_missing_observations(self):
        data, ctx, artifacts = self.example('weekly')
        current, future = date(2026, 10, 26), date(2026, 11, 2)
        ctx['unit'] = 'weekly'
        ctx['inputs'] = {}
        for segment, artifact in artifacts.items():
            artifact['state']['trained_through'] = '2025-12-22'
            for target in (current, future):
                ctx['inputs'][(target, segment)] = {'signature': segment, 'cutoff': current}
        for view in data['views'].values():
            view['points'][0]['date'] = current.isoformat()
            view['points'].append({**view['points'][0], 'date': future.isoformat(), 'is_current': False})
        result = describe_graph(data, ctx, artifacts)
        points = result['views']['regular']['points']
        self.assertEqual([p['model_details']['projection_steps'] for p in points], [44, 45])
        self.assertEqual([p['model_details']['missing_observation_periods_before_current'] for p in points], [43, 43])
        self.assertEqual(points[0]['model_details']['source_freshness']['expected_latest_closed_period'], '2026-10-19')

    def test_current_annual_metadata_describes_remaining_monthly_path(self):
        data, ctx, artifacts = self.example('yearly')
        current, future = date(2026, 1, 1), date(2027, 1, 1)
        cutoff = date(2026, 10, 1)
        for view in data['views'].values():
            view['points'][0]['date'] = current.isoformat()
            view['points'].append({**view['points'][0], 'date': future.isoformat(), 'is_current': False})
        ctx['inputs'] = {(target, segment): {'signature': segment, 'cutoff': cutoff}
                         for target in (current, future) for segment in ('regular', 'feeding')}
        for artifact in artifacts.values():
            artifact['state']['trained_through'] = '2025-12-01'
        result = describe_graph(data, ctx, artifacts)
        now, next_year = [p['model_details'] for p in result['views']['regular']['points']]
        self.assertIsNone(now['projection_steps'])
        self.assertEqual((now['projection_step_start'], now['projection_step_end']), (10, 12))
        self.assertEqual((next_year['projection_step_start'], next_year['projection_step_end']), (13, 24))
        self.assertEqual(now['projection_step_unit'], 'monthly')
        self.assertEqual(now['source_freshness']['missing_observation_periods'], 9)
        self.assertFalse(now['production_approved'])

    def test_missing_current_prediction_still_discloses_stale_source(self):
        data, ctx, artifacts = self.example()
        for view in data['views'].values():
            view['points'][0]['future_prediction'] = None
        for artifact in artifacts.values():
            artifact['state']['trained_through'] = '2025-12-01'
        result = describe_graph(data, ctx, artifacts)
        self.assertEqual(result['readiness']['status'], 'unavailable')
        self.assertEqual(result['readiness']['source_freshness']['status'], 'stale_data')
        self.assertEqual(result['views']['combined']['readiness']['source_freshness']['status'], 'stale_data')
        self.assertIsNone(result['views']['regular']['model_details'])

    def test_source_age_uses_last_observation_not_trailing_missing_bucket(self):
        data, ctx, artifacts = self.example()
        artifacts['feeding']['state']['values'] = [1, 2, None]
        result = describe_graph(data, ctx, artifacts)
        fresh = result['views']['regular']['model_details']['source_freshness']
        stale = result['views']['feeding']['model_details']['source_freshness']
        self.assertEqual(fresh['status'], 'current')
        self.assertEqual(stale['latest_observed_period'], '2026-08-01')
        self.assertEqual(stale['missing_observation_periods'], 1)
        self.assertEqual(result['readiness']['source_freshness']['status'], 'stale_data')

    def test_combined_keeps_distinct_component_training_ends_and_steps(self):
        data, ctx, artifacts = self.example()
        artifacts['feeding']['state']['trained_through'] = '2025-12-01'
        result = describe_graph(data, ctx, artifacts)
        combined = result['views']['combined']
        regular, feeding = [combined['model_details']['components'][segment]
                            for segment in ('regular', 'feeding')]
        self.assertEqual((regular['projection_steps'], feeding['projection_steps']), (1, 10))
        self.assertEqual(regular['source_freshness']['status'], 'current')
        self.assertEqual(feeding['source_freshness']['status'], 'stale_data')
        self.assertEqual(combined['readiness']['source_freshness']['status'], 'stale_data')
        self.assertFalse(combined['model_details']['production_approved'])
        self.assertEqual(combined['points'][0]['future_prediction'], 120)

    def test_missing_model_still_uses_known_source_age_without_claiming_a_fit(self):
        data, ctx, artifacts = self.example()
        for item in ctx['inputs'].values():
            item['rows'] = [{'date': '2025-12-01', 'actual': 100}]
        result = describe_graph(data, ctx, {})
        details = result['views']['regular']['model_details']
        self.assertIsNone(details['name'])
        self.assertIsNone(details['projection_steps'])
        self.assertEqual(details['source_freshness']['status'], 'stale_data')
        self.assertFalse(details['source_freshness']['coverage_verified'])

    def test_primary_rolling_backtest_and_baseline_loss_are_explicit(self):
        data, ctx, artifacts = self.example()
        day = date(2026, 10, 1)
        ctx['inputs'][(day, 'regular')]['primary'] = {'name': 'saved_sarima'}
        artifact = artifacts['regular']
        artifact['state'].update(name='saved_sarima', specification={
            'order': [1, 0, 0], 'seasonal_order': [1, 0, 0, 12]})
        artifact.update(selection='reuse_primary_monthly_sarima_selection',
                        primary_monthly_backtest={'basis': 'rolling_one_month_ahead',
                            'metrics': {'wape_percent': 20, 'periods': 12},
                            'baseline_metrics': {'wape_percent': 15, 'periods': 12},
                            'selected_baseline': 'previous_month'})
        result = describe_graph(data, ctx, artifacts)
        details = result['views']['regular']['model_details']
        self.assertEqual(details['family'], 'SARIMA')
        self.assertTrue(details['seasonal_terms_enabled'])
        self.assertEqual(details['selection_basis'], 'reuse_primary_monthly_sarima_selection')
        self.assertEqual(details['primary_backtest']['basis'], 'rolling_one_month_ahead')
        self.assertEqual(details['baseline_comparison']['result'], 'worse')
        self.assertEqual(details['baseline_comparison']['selected_baseline'], 'previous_month')
        self.assertIsNone(details['test'])

    def test_local_level_has_explicit_nonseasonal_label(self):
        data, ctx, artifacts = self.example()
        artifacts['feeding']['state'].update(name='local_level', specification={
            'order': [0, 1, 0], 'seasonal_order': [0, 0, 0, 0]})
        details = describe_graph(data, ctx, artifacts)['views']['feeding']['model_details']
        self.assertEqual(details['family'], 'ARIMA')
        self.assertIn('Local level', details['display_label'])
        self.assertIn('nonseasonal', details['display_label'])
        self.assertFalse(details['seasonal_terms_enabled'])

    def test_flat_primary_validation_is_not_mistaken_for_candidate_names(self):
        data, ctx, artifacts = self.example()
        artifacts['regular'].update(validation={'wape_percent': 24.0, 'periods': 13},
                                    validation_basis='rolling_one_week_ahead')
        details = describe_graph(data, ctx, artifacts)['views']['regular']['model_details']
        self.assertEqual(details['candidate_models'], ['ar1'])
        self.assertEqual(details['validation']['wape_percent'], 24.0)
        self.assertEqual(details['validation']['periods'], 13)
        self.assertEqual(details['validation_basis'], 'rolling_one_week_ahead')
