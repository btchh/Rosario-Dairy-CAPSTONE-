"""The extended outlook retains the centered API and saved forecast evidence.

These tests use synthetic states and mocked projection only. They never read or
write business records, fit models, or require a database.
"""
from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.test import SimpleTestCase

from . import beta_graph as graph


class SeasonalOutlookContractTests(SimpleTestCase):
    as_of = date(2026, 10, 28)

    def example(self, period='weekly', *, scope='real', feeding_available=True):
        current = graph.period_start(self.as_of, period)
        unit = 'weekly' if period == 'weekly' else 'monthly'
        calendar = graph.window(period, self.as_of)
        ctx = {'period': period, 'scope': scope, 'as_of': self.as_of,
               'calendar': calendar, 'unit': unit, 'inputs': {}, 'sales': [],
               'revision': 'synthetic-source-only'}
        artifacts = {}
        for target in calendar:
            cutoff = min(target, current)
            for segment in ('regular', 'feeding'):
                signature = f'{segment}:{cutoff}'
                ctx['inputs'][(target, segment)] = {
                    'signature': signature, 'cutoff': cutoff, 'primary': None,
                    'source_revision': 'synthetic-source-only',
                    'rows': [{'date': '2025-12-22' if unit == 'weekly' else '2025-12-01',
                              'actual': 100.0}],
                }
                state = {'name': segment, 'trained_through':
                         '2025-12-22' if unit == 'weekly' else '2025-12-01'}
                artifacts[signature] = ({'status': 'provisional', 'state': state}
                                        if segment == 'regular' or feeding_available else
                                        {'status': 'insufficient_data'})
        data = {'period': period, 'scope': scope, 'as_of': self.as_of.isoformat(), 'views': {}}
        for view in graph.VIEWS:
            points = []
            for target in calendar:
                future = target >= current
                value = self.value(view, target, current, unit)
                if not feeding_available and view in ('feeding', 'combined'):
                    value = None
                points.append({
                    'date': target.isoformat(),
                    'end_date': graph.period_end(target, period).isoformat(),
                    'actual': (25.0 if view == 'combined' else 20.0 if view == 'regular' else 5.0)
                              if target == current else None,
                    'past_prediction': None if future else value,
                    'future_prediction': value if future else None,
                    'prediction_basis': 'forecast' if future else 'historical_test',
                    'is_current': target == current, 'is_partial_period': target == current,
                    'issued_prediction': 111.0 if target == current else None,
                    'issued_provenance': {'run_id': 17} if target == current else None,
                    'forecast_origin': min(target, current).isoformat(),
                    'horizon': graph._horizon(target, min(target, current), period),
                    'horizon_unit': period,
                    'outlook_basis': 'latest_closed_period_forecast' if future else None,
                    'is_nowcast': False, 'annual_outlook': None,
                })
            data['views'][view] = {'label': graph.LABELS[view], 'points': points,
                                   'evaluation': {}, 'message': None}
        return ctx, data, artifacts

    @staticmethod
    def value(view, target, current, unit):
        offset = ((target-current).days//7 if unit == 'weekly' else
                  (target.year-current.year)*12+target.month-current.month)
        regular, feeding = 1000.0+10*offset, 2000.0+20*offset
        return regular+feeding if view == 'combined' else regular if view == 'regular' else feeding

    def projection(self, current):
        def project(component, targets, period):
            segment = component['state']['name']
            return [{'date': target,
                     'predicted': self.value(segment, date.fromisoformat(target), current, period),
                     'trained_through': component['state']['trained_through']}
                    for target in targets]
        return project

    def outlook(self, ctx, data, artifacts):
        current = graph.period_start(ctx['as_of'], ctx['period'])
        with patch('forecasting.beta_graph.future_rows', side_effect=self.projection(current)):
            return graph.build_seasonal_outlook(ctx, data, artifacts)

    def test_weekly_and_monthly_outlooks_include_one_full_cycle_from_current(self):
        for period, count in (('weekly', 52), ('monthly', 12)):
            with self.subTest(period=period):
                ctx, data, artifacts = self.example(period)
                original = deepcopy((ctx, data, artifacts))
                result = self.outlook(ctx, data, artifacts)
                current = graph.period_start(self.as_of, period)
                last = graph.advance(current, period, count-1)
                self.assertEqual(result['period'], period)
                self.assertEqual(result['as_of'], self.as_of.isoformat())
                self.assertEqual(result['range'], {
                    'start_date': current.isoformat(),
                    'end_date': graph.period_end(last, period).isoformat(), 'periods': count})
                self.assertEqual(set(result['views']), set(graph.VIEWS))
                for view in result['views'].values():
                    points = view['points']
                    self.assertEqual(len(points), count)
                    self.assertEqual([p['date'] for p in points],
                                     [graph.advance(current, period, i).isoformat() for i in range(count)])
                    self.assertEqual([p['horizon'] for p in points], list(range(1, count+1)))
                    self.assertEqual({p['forecast_origin'] for p in points}, {current.isoformat()})
                    self.assertEqual({p['horizon_unit'] for p in points}, {period})
                    self.assertTrue(all(p['past_prediction'] is None for p in points))
                    self.assertTrue(all(p['prediction_basis'] == 'forecast' for p in points))
                    self.assertEqual([p['is_current'] for p in points], [True]+[False]*(count-1))
                    self.assertTrue(all(p['actual'] is None for p in points[1:]))
                self.assertEqual((ctx, data, artifacts), original)

    def test_first_four_values_and_existing_issued_comparisons_match_centered_api(self):
        for period in ('weekly', 'monthly'):
            with self.subTest(period=period):
                ctx, data, artifacts = self.example(period)
                result = self.outlook(ctx, data, artifacts)
                for view in graph.VIEWS:
                    centered = [p for p in data['views'][view]['points'] if p['future_prediction'] is not None]
                    for old, expanded in zip(centered, result['views'][view]['points']):
                        for key in old:
                            self.assertEqual(expanded[key], old[key], (period, view, key))
                    for point in result['views'][view]['points'][4:]:
                        self.assertIsNone(point['issued_prediction'])
                        self.assertIsNone(point['issued_provenance'])

    def test_extended_path_projects_each_component_once_from_its_saved_state(self):
        ctx, data, artifacts = self.example()
        current = graph.period_start(self.as_of, 'weekly')
        with patch('forecasting.beta_graph.future_rows', side_effect=self.projection(current)) as project, \
                patch('forecasting.beta_graph._train', side_effect=AssertionError('Do not fit an outlook')):
            result = graph.build_seasonal_outlook(ctx, data, artifacts)
        self.assertEqual(project.call_count, 2)
        self.assertEqual({call.args[0]['state']['name'] for call in project.call_args_list},
                         {'regular', 'feeding'})
        for call in project.call_args_list:
            self.assertEqual(call.args[0]['state']['trained_through'], '2025-12-22')
            self.assertEqual(call.args[1][-1], result['views']['regular']['points'][-1]['date'])
            self.assertEqual(call.args[2], 'weekly')

    def test_combined_is_exact_component_sum_and_preserves_separate_views(self):
        ctx, data, artifacts = self.example()
        result = self.outlook(ctx, data, artifacts)
        rows = zip(*(result['views'][view]['points'] for view in ('combined', 'regular', 'feeding')))
        for combined, regular, feeding in rows:
            self.assertEqual(combined['future_prediction'],
                             round(regular['future_prediction']+feeding['future_prediction'], 2))
            self.assertNotEqual(regular['future_prediction'], feeding['future_prediction'])

    def test_missing_feeding_never_turns_combined_into_regular_or_zero(self):
        ctx, data, artifacts = self.example(feeding_available=False)
        result = self.outlook(ctx, data, artifacts)
        self.assertTrue(all(p['future_prediction'] is not None
                            for p in result['views']['regular']['points']))
        for view in ('feeding', 'combined'):
            self.assertTrue(all(p['future_prediction'] is None for p in result['views'][view]['points']))

    def test_saved_outlook_replays_without_fitting_or_projecting_and_updates_current_actual_only(self):
        ctx, data, artifacts = self.example()
        saved = self.outlook(ctx, data, artifacts)
        frozen = deepcopy(saved)
        for view in graph.VIEWS:
            current = next(p for p in data['views'][view]['points'] if p['is_current'])
            current['actual'] = 777.0
        with patch('forecasting.beta_graph.future_rows', side_effect=AssertionError('GET must replay saved path')), \
                patch('forecasting.beta_graph.project', side_effect=AssertionError('GET must not project')), \
                patch('forecasting.beta_graph._train', side_effect=AssertionError('GET must not train')), \
                patch('forecasting.beta_graph.fit', side_effect=AssertionError('GET must not optimize')):
            result = graph.build_seasonal_outlook(ctx, data, artifacts,
                                                 saved_outlook=saved, allow_projection=False)
        self.assertEqual(saved, frozen)
        for view in graph.VIEWS:
            points = result['views'][view]['points']
            self.assertEqual(points[0]['actual'], 777.0)
            self.assertTrue(all(p['actual'] is None for p in points[1:]))
            self.assertEqual([p['future_prediction'] for p in points],
                             [p['future_prediction'] for p in frozen['views'][view]['points']])

    def test_read_without_saved_outlook_withholds_predictions_without_projection(self):
        ctx, data, artifacts = self.example()
        for view in data['views'].values():
            for point in view['points']:
                point['future_prediction'] = None
        with patch('forecasting.beta_graph.future_rows', side_effect=AssertionError('GET cannot fill missing state')):
            result = graph.build_seasonal_outlook(ctx, data, artifacts, allow_projection=False)
        self.assertTrue(all(p['future_prediction'] is None for view in result['views'].values()
                            for p in view['points']))

    def test_yearly_retains_distinct_annual_semantics_without_a_synthetic_cycle(self):
        ctx = {'period': 'yearly'}
        with patch('forecasting.beta_graph.future_rows', side_effect=AssertionError('Yearly needs no expansion')):
            self.assertIsNone(graph.build_seasonal_outlook(ctx, {}, {}))

    def test_bad_component_projection_withholds_its_whole_extended_path(self):
        """An invalid tail must not publish a plausible prefix as a complete path."""
        ctx, data, artifacts = self.example()
        current = graph.period_start(self.as_of, 'weekly')
        valid_projection = self.projection(current)
        for defect in ('truncated', 'wrong_date', 'reversed', 'nan', 'infinity',
                       'negative', 'unsupported_currency', 'missing_value'):
            with self.subTest(defect=defect):
                def damaged_projection(component, targets, period):
                    rows = valid_projection(component, targets, period)
                    if component['state']['name'] != 'regular':
                        return rows
                    if defect == 'truncated':
                        return rows[:-1]
                    if defect == 'wrong_date':
                        rows[-1]['date'] = '2099-01-05'
                    elif defect == 'reversed':
                        rows.reverse()
                    elif defect == 'missing_value':
                        rows[-1].pop('predicted')
                    else:
                        rows[-1]['predicted'] = {
                            'nan': float('nan'), 'infinity': float('inf'),
                            'negative': -0.01, 'unsupported_currency': 1e18,
                        }[defect]
                    return rows

                with patch('forecasting.beta_graph.future_rows', side_effect=damaged_projection):
                    result = graph.build_seasonal_outlook(ctx, data, artifacts)
                for view in ('regular', 'combined'):
                    points = result['views'][view]['points']
                    self.assertTrue(all(p['future_prediction'] is None for p in points[4:]))
                    self.assertEqual([p['future_prediction'] for p in points[:4]],
                                     [p['future_prediction'] for p in data['views'][view]['points'][3:]])
                self.assertTrue(all(p['future_prediction'] is not None
                                    for p in result['views']['feeding']['points']))

    def test_projection_error_cannot_fall_back_to_fabricated_seasonal_values(self):
        ctx, data, artifacts = self.example()
        with patch('forecasting.beta_graph.future_rows', side_effect=ValueError('Unsupported projection')):
            result = graph.build_seasonal_outlook(ctx, data, artifacts)
        for view in graph.VIEWS:
            points = result['views'][view]['points']
            self.assertTrue(all(p['future_prediction'] is None for p in points[4:]))
            self.assertEqual([p['future_prediction'] for p in points[:4]],
                             [p['future_prediction'] for p in data['views'][view]['points'][3:]])

    def test_mismatched_saved_dates_are_withheld_without_regenerating_predictions(self):
        ctx, data, artifacts = self.example()
        saved = self.outlook(ctx, data, artifacts)
        saved['views']['regular']['points'][-1]['date'] = '2099-01-05'
        frozen = deepcopy(saved)
        with patch('forecasting.beta_graph.future_rows', side_effect=AssertionError('Invalid saved path stays unavailable')):
            result = graph.build_seasonal_outlook(ctx, data, artifacts,
                                                 saved_outlook=saved, allow_projection=False)
        self.assertEqual(saved, frozen)
        self.assertTrue(all(p['future_prediction'] is None
                            for p in result['views']['regular']['points']))
        self.assertTrue(all(p['future_prediction'] is not None
                            for p in result['views']['feeding']['points']))

    def test_malformed_saved_shapes_and_values_remain_unavailable_without_get_work(self):
        ctx, data, artifacts = self.example()
        saved = self.outlook(ctx, data, artifacts)
        defects = ('outlook_none', 'outlook_list', 'outlook_string',
                   'views_none', 'views_list', 'views_string',
                   'view_none', 'view_list', 'view_string',
                   'points_none', 'points_string', 'points_empty',
                   'point_none', 'point_list', 'point_string',
                   'missing_future', 'negative_future', 'nan_future',
                   'infinite_future', 'boolean_future', 'string_future', 'past_value')
        for defect in defects:
            with self.subTest(defect=defect):
                damaged = deepcopy(saved)
                if defect.startswith('outlook_'):
                    damaged = {'outlook_none': None, 'outlook_list': [],
                               'outlook_string': 'damaged'}[defect]
                elif defect.startswith('views_'):
                    damaged['views'] = {'views_none': None, 'views_list': [],
                                        'views_string': 'damaged'}[defect]
                elif defect.startswith('view_'):
                    damaged['views']['regular'] = {'view_none': None, 'view_list': [],
                                                   'view_string': 'damaged'}[defect]
                elif defect.startswith('points_'):
                    damaged['views']['regular']['points'] = {
                        'points_none': None, 'points_string': 'damaged', 'points_empty': []}[defect]
                elif defect.startswith('point_'):
                    damaged['views']['regular']['points'][-1] = {
                        'point_none': None, 'point_list': [], 'point_string': 'damaged'}[defect]
                else:
                    point = damaged['views']['regular']['points'][-1]
                    if defect == 'missing_future':
                        point.pop('future_prediction')
                    elif defect == 'past_value':
                        point['past_prediction'] = 123.0
                        # A matching whole-run digest is not semantic evidence
                        # that a future-only path can contain a historical line.
                        run = {'seasonal_outlook': damaged}
                        run['checksum'] = graph.digest(run)
                        damaged = graph.checked(run)['seasonal_outlook']
                    else:
                        point['future_prediction'] = {
                            'negative_future': -0.01, 'nan_future': float('nan'),
                            'infinite_future': float('inf'), 'boolean_future': True,
                            'string_future': '123.0',
                        }[defect]
                with patch('forecasting.beta_graph.future_rows', side_effect=AssertionError('GET must not project')), \
                        patch('forecasting.beta_graph._train', side_effect=AssertionError('GET must not train')), \
                        patch('forecasting.beta_graph.fit', side_effect=AssertionError('GET must not fit')):
                    result = graph.build_seasonal_outlook(ctx, data, artifacts,
                                                         saved_outlook=damaged, allow_projection=False)
                self.assertTrue(all(p['future_prediction'] is None
                                    for p in result['views']['regular']['points']))
                if defect.startswith(('outlook_', 'views_')):
                    self.assertTrue(all(p['future_prediction'] is None
                                        for view in result['views'].values() for p in view['points']))

    def test_eligible_weekly_regular_uses_seasonal_adapter_and_keeps_feeding_independent(self):
        ctx, _, artifacts = self.example()
        feeding_states = {}
        for (target, segment), item in ctx['inputs'].items():
            if segment == 'regular':
                item['weekly'] = {'status': 'saved_sarima', 'marker': item['signature']}
            else:
                feeding_states[item['signature']] = artifacts[item['signature']]
        adapted = {'status': 'provisional', 'state': {'name': 'weekly_sarima_100_100_52'}}
        with patch('forecasting.beta_graph.regular_weekly_component', return_value=adapted) as adapt, \
                patch('forecasting.beta_graph._train', side_effect=AssertionError('Do not replace eligible SARIMA')), \
                patch('forecasting.beta_graph._point', side_effect=lambda component, *_:
                      123.0 if component.get('state') else None), \
                patch('forecasting.beta_graph._issued_predictions', return_value={}):
            result = graph.build(ctx, feeding_states)
        self.assertEqual(adapt.call_count, 4)  # Three historical cutoffs plus one latest outlook origin.
        self.assertEqual({call.args[0]['status'] for call in adapt.call_args_list}, {'saved_sarima'})
        self.assertTrue(all(p['future_prediction'] == 123.0
                            for p in result['views']['regular']['points'][3:]))
        self.assertTrue(all(p['future_prediction'] == 246.0
                            for p in result['views']['combined']['points'][3:]))
        self.assertTrue(all(result['artifacts'][key] == state for key, state in feeding_states.items()))

    def test_invalid_eligible_weekly_fit_is_withheld_without_nonseasonal_substitution(self):
        ctx, _, artifacts = self.example()
        feeding_states = {}
        for (_, segment), item in ctx['inputs'].items():
            if segment == 'regular':
                item['weekly'] = {'status': 'invalid_saved_artifact'}
            else:
                feeding_states[item['signature']] = artifacts[item['signature']]
        with patch('forecasting.beta_graph.regular_weekly_component',
                   return_value={'status': 'invalid_primary_artifact'}), \
                patch('forecasting.beta_graph._train', side_effect=AssertionError('Invalid primary cannot fall back')), \
                patch('forecasting.beta_graph._point', side_effect=lambda component, *_:
                      123.0 if component.get('state') else None), \
                patch('forecasting.beta_graph._issued_predictions', return_value={}):
            result = graph.build(ctx, feeding_states)
        for view in ('regular', 'combined'):
            self.assertTrue(all(p['past_prediction'] is None and p['future_prediction'] is None
                                for p in result['views'][view]['points']))
        self.assertTrue(all(p['future_prediction'] == 123.0
                            for p in result['views']['feeding']['points'][3:]))

    def test_eligible_monthly_history_waits_for_its_primary_without_nonseasonal_substitution(self):
        ctx, _, artifacts = self.example('monthly')
        feeding_states = {}
        for (_, segment), item in ctx['inputs'].items():
            if segment == 'regular':
                item['primary_required'] = True
            else:
                feeding_states[item['signature']] = artifacts[item['signature']]
        with patch('forecasting.beta_graph._train', side_effect=AssertionError('Eligible monthly primary is required')), \
                patch('forecasting.beta_graph.regular_weekly_component',
                      side_effect=AssertionError('Monthly data cannot use the weekly adapter')), \
                patch('forecasting.beta_graph._point', side_effect=lambda component, *_:
                      123.0 if component.get('state') else None), \
                patch('forecasting.beta_graph._issued_predictions', return_value={}):
            result = graph.build(ctx, feeding_states)
        for view in ('regular', 'combined'):
            self.assertTrue(all(p['past_prediction'] is None and p['future_prediction'] is None
                                for p in result['views'][view]['points']))
        regular_states = [value for key, value in result['artifacts'].items() if key.startswith('regular:')]
        self.assertTrue(all(value['status'] == 'primary_refresh_required' for value in regular_states))

    def test_saved_outlook_rejects_wrong_origin_horizon_or_period_metadata(self):
        ctx, data, artifacts = self.example()
        saved = self.outlook(ctx, data, artifacts)
        for field, value in (('forecast_origin', '2025-01-01'), ('horizon', 1),
                             ('horizon_unit', 'monthly'), ('end_date', '2099-01-01'),
                             ('is_current', True), ('is_partial_period', True),
                             ('prediction_basis', 'issued_forecast')):
            with self.subTest(field=field):
                damaged = deepcopy(saved)
                damaged['views']['regular']['points'][-1][field] = value
                with patch('forecasting.beta_graph.future_rows', side_effect=AssertionError('No GET repair')):
                    result = graph.build_seasonal_outlook(ctx, data, artifacts,
                                                         saved_outlook=damaged, allow_projection=False)
                for view in ('regular', 'combined'):
                    self.assertTrue(all(p['future_prediction'] is None for p in result['views'][view]['points']))
                self.assertTrue(all(p['future_prediction'] is not None
                                    for p in result['views']['feeding']['points']))

    def test_saved_combined_path_must_equal_its_retained_components(self):
        ctx, data, artifacts = self.example()
        saved = self.outlook(ctx, data, artifacts)
        saved['views']['combined']['points'][-1]['future_prediction'] += 1
        with patch('forecasting.beta_graph.future_rows', side_effect=AssertionError('No GET repair')):
            result = graph.build_seasonal_outlook(ctx, data, artifacts,
                                                 saved_outlook=saved, allow_projection=False)
        self.assertTrue(all(p['future_prediction'] is None for p in result['views']['combined']['points']))
        for view in ('regular', 'feeding'):
            self.assertTrue(all(p['future_prediction'] is not None for p in result['views'][view]['points']))
