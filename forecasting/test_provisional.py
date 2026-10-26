from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.contrib.auth import get_user_model
from django.core.cache import cache
from config.test_support import APIClient
from .data import RecordedSale, SalesSnapshot
from .models import ProvisionalForecastRun, MonthlyIssuedForecast
from .provisional import (build, context, digest, fit, future_rows, project, refresh,
                          report, series, train)


def snapshot():
    rows = tuple(RecordedSale(i*2+j, date(2024+i//12, i%12+1, 15), 'c', 'Customer', segment,
                             Decimal(100+i*2+j*10))
                 for i in range(24) for j, segment in enumerate(('regular', 'feeding')))
    return SalesSnapshot(date(2026, 1, 1), 'real', rows, 20)


def component(rows, period):
    return {'status': 'provisional', 'state': fit(rows, 'local_level', 52 if period == 'weekly' else 12),
            'observed_periods': len(rows), 'missing_periods': 0, 'seasonal_terms_enabled': False}


class ProvisionalProjectionTests(SimpleTestCase):
    def test_seasonal_candidates_become_eligible_only_after_sufficient_earlier_observations(self):
        from .provisional import advance
        for period, short, enough, first in [('monthly',35,36,date(2023,1,1)),
                                             ('weekly',115,116,date(2023,1,2))]:
            rows=[{'date':advance(first,period,n).isoformat(),'actual':100+n} for n in range(enough)]
            def fitted(prefix,name,season):
                return {'name':name,'trained_through':prefix[-1]['date']}
            def evaluated(state,held,frequency):
                return {'wape':0 if state['name']=='seasonal_ar' else 1,'rows':[]}
            with patch('forecasting.provisional.fit',side_effect=fitted), \
                    patch('forecasting.provisional.evaluate',side_effect=evaluated), \
                    patch('forecasting.provisional.project',return_value=[{'predicted':1}]):
                before=train(rows[:short],period)
                after=train(rows,period)
            self.assertNotIn('seasonal_ar',before['validation'])
            self.assertIn('seasonal_ar',after['validation'])
            self.assertTrue(after['seasonal_terms_enabled'])

    def test_unknown_periods_are_null_and_unclassified_is_excluded(self):
        snap = snapshot()
        snap = SalesSnapshot(snap.as_of, snap.revision,
            tuple(r for r in snap.sales if r.day.month != 2)+
            (RecordedSale(999, date(2025, 2, 1), '', '', 'unclassified', Decimal('999999')),), 20)
        rows = series(snap, 'feeding', 'monthly')
        self.assertIsNone(rows[1]['actual'])
        self.assertIsNone(rows[13]['actual'])
        state = fit(rows, 'local_level', 12)
        self.assertIsNone(state['values'][1])
        self.assertEqual(state['values'][0]*state['scale'], rows[0]['actual'])

    def test_portable_state_forecasts_multiple_steps_without_optimization(self):
        rows = series(snapshot(), 'regular', 'monthly')
        state = fit(rows, 'local_level', 12)
        import json
        state = json.loads(json.dumps(state, allow_nan=False))
        with patch('statsmodels.tsa.statespace.sarimax.SARIMAX.fit', side_effect=AssertionError('GET must not fit')):
            points = future_rows({'state': state}, ['2027-01-01', '2028-01-01'], 'yearly')
            path = project(state, 36)
        self.assertEqual(points[0]['predicted'], round(sum(r['predicted'] for r in path[12:24]), 2))
        self.assertEqual(points[1]['predicted'], round(sum(r['predicted'] for r in path[24:36]), 2))
        self.assertFalse(points[0]['production_approved'])
        self.assertEqual(points[0]['basis'], 'single_origin_monthly_path_sum')

    @patch('forecasting.provisional.primary_monthly', return_value={
        'status': 'fallback_allowed', 'dependency': None, 'fallback_reason': 'short_history'})
    def test_all_nine_combinations_have_future_points_and_reconcile(self, primary):
        snap = snapshot()
        with patch('forecasting.provisional.load_snapshot', return_value=snap):
            snapshots, targets, _ = context(date(2026, 10, 22))
        with patch('forecasting.provisional.train', side_effect=component):
            result = build(snapshots, targets)
        primary.assert_called()
        for period, views in result['periods'].items():
            self.assertEqual(set(views), {'regular', 'feeding', 'combined'})
            for key, view in views.items():
                self.assertEqual(len(view['points']), {'weekly': 13, 'monthly': 12, 'yearly': 2}[period])
                self.assertTrue(all(date.fromisoformat(r['date']) > date(2026, 10, 22) for r in view['points']))
                self.assertTrue(all(r['lower_bound'] <= r['predicted'] <= r['upper_bound'] for r in view['points']))
            for r, f, c in zip(*(views[k]['points'] for k in ('regular', 'feeding', 'combined'))):
                self.assertAlmostEqual(c['predicted'], r['predicted']+f['predicted'], places=2)
            if period == 'yearly':
                self.assertIsNone(views['regular']['metrics'])
        with patch('forecasting.provisional.train', side_effect=AssertionError('unchanged inputs must reuse')):
            self.assertEqual(build(snapshots, targets, result), result)

    def test_short_history_disables_seasonal_terms_and_selection_precedes_test(self):
        rows = series(snapshot(), 'feeding', 'monthly')[-10:]
        calls = []
        def fitted(prefix, name, season):
            calls.append((len(prefix), name))
            return {'name': name, 'trained_through': prefix[-1]['date']}
        def evaluated(state, held_out, period):
            return {'wape': 1 if state['name'] == 'ar1' else 2, 'rows': []}
        with patch('forecasting.provisional.fit', side_effect=fitted), \
                patch('forecasting.provisional.evaluate', side_effect=evaluated), \
                patch('forecasting.provisional.project', return_value=[{'predicted': 1}]):
            result = train(rows, 'monthly')
        self.assertEqual(calls, [(6, 'local_level'), (6, 'ar1'), (8, 'ar1'), (10, 'ar1')])
        self.assertFalse(result['seasonal_terms_enabled'])
        self.assertEqual(result['state']['name'], 'ar1')

    def test_too_little_history_and_failed_fits_never_invent_zero(self):
        rows = series(snapshot(), 'feeding', 'monthly')
        self.assertEqual(train(rows[:5], 'monthly')['status'], 'insufficient_data')
        with patch('forecasting.provisional.fit', side_effect=ValueError('bad fit')):
            self.assertEqual(train(rows, 'monthly')['status'], 'fit_failed')
        self.assertEqual(future_rows({}, ['2027-01-01'], 'yearly'), [])

    def test_test_failure_cannot_replace_the_validation_winner(self):
        rows = series(snapshot(), 'feeding', 'monthly')[-10:]
        def fitted(prefix, name, season):
            return {'name': name, 'trained_through': prefix[-1]['date']}
        def evaluated(state, held_out, period):
            # Six months train validation, eight train the test origin.
            if state['trained_through'] == rows[7]['date']:
                raise ValueError('test projection failed')
            return {'wape': 1 if state['name'] == 'ar1' else 2, 'rows': []}
        with patch('forecasting.provisional.fit', side_effect=fitted) as fitting, \
                patch('forecasting.provisional.evaluate', side_effect=evaluated):
            result = train(rows, 'monthly')
        self.assertEqual(result['status'], 'fit_failed')
        self.assertEqual(result['selected_candidate'], 'ar1')
        self.assertNotIn('state', result)
        self.assertEqual([(len(c.args[0]), c.args[1]) for c in fitting.call_args_list],
                         [(6, 'local_level'), (6, 'ar1'), (8, 'ar1')])

    def test_final_refit_failure_cannot_replace_the_validation_winner(self):
        rows = series(snapshot(), 'feeding', 'monthly')[-10:]
        def fitted(prefix, name, season):
            if len(prefix) == len(rows):
                raise ValueError('final refit failed')
            return {'name': name, 'trained_through': prefix[-1]['date']}
        with patch('forecasting.provisional.fit', side_effect=fitted) as fitting, \
                patch('forecasting.provisional.evaluate', side_effect=lambda state, held, period:
                      {'wape': 1 if state['name'] == 'ar1' else 2, 'rows': []}):
            result = train(rows, 'monthly')
        self.assertEqual(result['status'], 'fit_failed')
        self.assertEqual(result['selected_candidate'], 'ar1')
        self.assertEqual([c.args[1] for c in fitting.call_args_list],
                         ['local_level', 'ar1', 'ar1', 'ar1'])

    def test_digest_survives_signed_zero_json_database_roundtrip(self):
        self.assertEqual(digest({'value': -0.0}), digest({'value': 0.0}))


class ProvisionalPersistenceTests(TestCase):
    def test_new_completed_periods_retrain_both_segments_but_partial_periods_reuse(self):
        from django.utils import timezone
        from sales.models import Transaction
        user=get_user_model().objects.create_user(username='growth-only-test',email='growth-only@example.test')
        for month in range(1,7):
            for segment,base in [('regular',100),('feeding',400)]:
                sale=Transaction.objects.create(handled_by=user,total_amount=base+month*10,
                    source_sales_segment=segment,source_reference=f'2025:TEST-GROWTH:{segment}:{month}')
                Transaction.objects.filter(pk=sale.pk).update(created_at=timezone.make_aware(datetime(2025,month,15)))
        first,_=refresh(date(2025,7,1))
        first.refresh_from_db()  # Compare persisted JSON rather than Python tuples.
        old=deepcopy(first.findings)
        for segment,total in [('regular',900),('feeding',1500)]:
            sale=Transaction.objects.create(handled_by=user,total_amount=total,source_sales_segment=segment,
                source_reference=f'2025:TEST-GROWTH:{segment}:7')
            Transaction.objects.filter(pk=sale.pk).update(created_at=timezone.make_aware(datetime(2025,7,15)))
        with patch('forecasting.provisional.train',side_effect=AssertionError('Unfinished periods cannot train')):
            partial,_=refresh(date(2025,7,17))
        for key in old['components']:
            self.assertEqual(old['components'][key]['input_signature'],partial.findings['components'][key]['input_signature'])
        with patch('forecasting.provisional.train',wraps=train) as fitting:
            weekly,_=refresh(date(2025,7,21))
        self.assertEqual(fitting.call_count,2)
        for segment in ('regular','feeding'):
            self.assertEqual(weekly.findings['components'][segment+'_weekly']['observed_periods'],7)
            self.assertEqual(weekly.findings['components'][segment+'_monthly']['observed_periods'],6)
        with patch('forecasting.provisional.train',wraps=train) as fitting:
            monthly,_=refresh(date(2025,8,1))
        self.assertEqual(fitting.call_count,2)
        for segment in ('regular','feeding'):
            component=monthly.findings['components'][segment+'_monthly']
            self.assertEqual(component['observed_periods'],7)
            self.assertEqual(component['state']['trained_through'],'2025-07-01')
            self.assertNotEqual(component['input_signature'],old['components'][segment+'_monthly']['input_signature'])
        first.refresh_from_db()
        self.assertEqual(first.findings,old)
        self.assertEqual(MonthlyIssuedForecast.objects.count(),0)

    def setUp(self):
        self.snap = snapshot()
        with patch('forecasting.provisional.load_snapshot', return_value=self.snap):
            self.ctx = context(date(2026, 10, 22))

    def test_refresh_reuses_exact_input_get_does_not_fit_and_corruption_is_withheld(self):
        with patch('forecasting.provisional.context', return_value=self.ctx), \
                patch('forecasting.provisional.train', side_effect=component):
            run, created = refresh()
        self.assertTrue(created)
        with patch('forecasting.provisional.context', return_value=self.ctx), \
                patch('forecasting.provisional.build', side_effect=AssertionError('no refit')):
            self.assertEqual(refresh()[0].pk, run.pk)
            result = report('monthly', date(2026, 10, 22))
        self.assertEqual(len(result['views']['feeding']['points']), 12)
        self.assertTrue(result['views']['feeding']['stale_history'])
        self.assertEqual(MonthlyIssuedForecast.objects.count(), 0)
        run.findings['periods']['monthly']['feeding']['points'][0]['predicted'] = 0
        run.save()
        with patch('forecasting.provisional.context', return_value=self.ctx):
            self.assertEqual(report('monthly')['status'], 'invalid_artifact')

    def test_source_change_during_training_does_not_save(self):
        changed = (*self.ctx[:2], 'changed')
        with patch('forecasting.provisional.context', side_effect=[self.ctx, changed]), \
                patch('forecasting.provisional.train', side_effect=component):
            with self.assertRaisesMessage(ValueError, 'changed during fitting'):
                refresh()
        self.assertEqual(ProvisionalForecastRun.objects.count(), 0)

    def test_missing_current_revision_requires_offline_refresh(self):
        with patch('forecasting.provisional.context', return_value=self.ctx):
            self.assertEqual(report('weekly')['status'], 'refresh_required')

    def test_chart_api_returns_all_nine_projections_without_fitting(self):
        with patch('forecasting.provisional.context', return_value=self.ctx), \
                patch('forecasting.provisional.train', side_effect=component):
            refresh()
        client = APIClient()
        user = get_user_model().objects.create_user(username='projection-admin', role='admin')
        client.force_authenticate(user)
        cache.clear()
        with patch('forecasting.provisional.context', return_value=self.ctx), \
                patch('forecasting.chart.model_context', return_value=(self.snap, None, None, {}, None, 'stale_data')), \
                patch('forecasting.chart.weekly_snapshot', return_value=self.snap), \
                patch('forecasting.chart.weekly_report', return_value={'test_rows': []}), \
                patch('statsmodels.tsa.statespace.sarimax.SARIMAX.fit', side_effect=AssertionError('GET must not fit')):
            for period in ('weekly', 'monthly', 'yearly'):
                response = client.get('/api/forecasting/chart/', {'period': period})
                self.assertEqual(response.status_code, 200)
                for view in response.data['views'].values():
                    self.assertEqual(view['provisional_status'], 'provisional')
                    self.assertTrue(view['provisional_forecast'])
                    self.assertEqual(view['forecast'], [])
