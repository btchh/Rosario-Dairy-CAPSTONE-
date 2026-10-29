"""Three graph series, chronological fits, original predictions and demo isolation."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.core.management import call_command, CommandError
from django.utils import timezone

from config.test_support import APITestCase
from sales.models import Transaction
from . import beta_graph as graph
from .data import RecordedSale, SalesSnapshot, load_snapshot
from .learning import ENGINE_VERSION, month_revision, shift_month
from .models import GraphForecastRun, GraphPrediction, MonthlyCoverage
from .provisional import digest


def fake_train(rows, period):
    if not rows:
        return {'status': 'insufficient_data'}
    return {'status': 'provisional', 'state': {'trained_through': rows[-1]['date']}}


def fake_point(component, target, period):
    return 123.0 if component.get('state') else None


class CenteredWindowTests(SimpleTestCase):
    def test_week_month_year_ranges_are_centered_and_calendar_correct(self):
        cases = [('weekly', date(2026, 1, 1), '2025-12-08', '2026-01-19', 7, 3),
                 ('monthly', date(2026, 1, 31), '2025-10-01', '2026-04-01', 7, 3),
                 ('yearly', date(2026, 10, 23), '2024-01-01', '2028-01-01', 5, 2)]
        for period, today, first, last, count, middle in cases:
            points = graph.window(period, today)
            self.assertEqual((len(points), points[0].isoformat(), points[-1].isoformat()), (count, first, last))
            self.assertEqual(points[middle], graph.period_start(today, period))

    def test_primary_sarima_is_only_reused_for_matching_earlier_values(self):
        from .seasonal import SARIMA_CONFIGS
        spec = SARIMA_CONFIGS['sarima_010_100_12']
        rows = [{'date': f'2025-{m:02d}-01', 'actual': 100.0} for m in range(1, 13)]
        rows = [{'date': f'2024-{m:02d}-01', 'actual': 100.0} for m in range(1, 13)]+rows
        artifact = {'name': 'sarima_010_100_12', 'family': 'SARIMA', 'converged': True,
                    'training_months': 24, 'normalized_history': [1.0]*24, 'scale': 100,
                    'trained_through': '2025-12-01', 'params': [0.5, 0.1], 'param_names': ['ar.S.L12', 'sigma2'],
                    **{k: list(v) if isinstance(v, tuple) else v for k, v in spec.items()}}
        run = SimpleNamespace(findings={'artifact': artifact, 'artifact_sha256': digest(artifact),
                                       'selected_candidate': artifact['name']})
        with patch('forecasting.beta_graph.MonthlyModelRun.objects.filter', return_value=[run]):
            state = graph._primary(SimpleNamespace(revision='source', as_of=date(2026,1,1)), rows)
            self.assertEqual(state['name'], artifact['name'])
            # The primary engine intentionally selects the latest contiguous
            # block, even when the ledger also has older isolated source rows.
            with_old_gaps = [{'date': '2023-10-01', 'actual': 20.0},
                             {'date': '2023-11-01', 'actual': None},
                             {'date': '2023-12-01', 'actual': None}]+rows
            reused = graph._primary(SimpleNamespace(revision='source', as_of=date(2026,1,1)), with_old_gaps)
            self.assertEqual(reused, state)
            self.assertEqual(reused['training_start'], '2024-01-01')
            # A gap within the fitted window or a newer observation invalidates
            # reuse; selecting an arbitrary matching subsection would leak data.
            missing = deepcopy(rows)
            missing[5]['actual'] = None
            self.assertIsNone(graph._primary(SimpleNamespace(revision='source', as_of=date(2026,1,1)), missing))
            extended = rows+[{'date':'2026-01-01', 'actual':100.0}]
            self.assertIsNone(graph._primary(SimpleNamespace(revision='source', as_of=date(2026,2,1)), extended))
            changed = deepcopy(rows)
            changed[-1]['actual'] = 999
            self.assertIsNone(graph._primary(SimpleNamespace(revision='source', as_of=date(2026,1,1)), changed))

    def test_reviewed_zero_month_keeps_the_exact_primary_model_and_changes_graph_identity(self):
        from .seasonal import SARIMA_CONFIGS
        missing = date(2024, 2, 1)
        months = [shift_month(date(2023, 1, 1), n) for n in range(36)]
        sales = tuple(RecordedSale(n+1, month.replace(day=15), 'customer', 'Customer',
                                 'regular', Decimal('100'))
                      for n, month in enumerate(months) if month != missing)
        def snapshot(cutoff):
            return SalesSnapshot(cutoff, f'source-{cutoff}',
                                 tuple(sale for sale in sales if sale.day < cutoff), 0)
        spec = SARIMA_CONFIGS['sarima_010_100_12']
        artifact = {'name': 'sarima_010_100_12', 'family': 'SARIMA', 'converged': True,
                    'training_months': 36, 'normalized_history': [0.0 if m == missing else 1.0 for m in months],
                    'scale': 100.0, 'trained_through': '2025-12-01', 'params': [0.5, 0.1],
                    'param_names': ['ar.S.L12', 'sigma2'],
                    **{k: list(v) if isinstance(v, tuple) else v for k, v in spec.items()}}
        run = SimpleNamespace(findings={'artifact': artifact, 'artifact_sha256': digest(artifact),
                                       'selected_candidate': artifact['name']})
        def matching_run(**lookup):
            self.assertEqual(lookup['engine_version'], ENGINE_VERSION)
            return [run] if lookup['source_revision'] == 'source-2026-01-01' else []
        with patch('forecasting.beta_graph.load_snapshots',
                   side_effect=lambda cutoffs: {day: snapshot(day) for day in cutoffs}), \
                patch('forecasting.beta_graph.MonthlyModelRun.objects.filter', side_effect=matching_run), \
                patch('forecasting.beta_graph.valid_coverage', return_value={}) as coverage, \
                patch('forecasting.beta_graph.fit', side_effect=AssertionError('Context must not fit')):
            unknown = graph.context('monthly', 'real', date(2026, 1, 15))
            coverage.return_value = {missing: SimpleNamespace(confirmed_zero=True)}
            reviewed = graph.context('monthly', 'real', date(2026, 1, 15))
        key = (date(2026, 1, 1), 'regular')
        self.assertIsNone(unknown['inputs'][key]['primary'])
        state = reviewed['inputs'][key]['primary']
        self.assertEqual(state['name'], artifact['name'])
        self.assertEqual(state['values'], artifact['normalized_history'])
        self.assertEqual(state['training_start'], '2023-01-01')
        self.assertNotEqual(unknown['inputs'][key]['signature'], reviewed['inputs'][key]['signature'])
        self.assertNotEqual(unknown['revision'], reviewed['revision'])

    def test_reviewed_zero_month_does_not_fill_another_unknown_month(self):
        snap = SalesSnapshot(date(2026, 1, 1), 'source', (
            RecordedSale(1, date(2025, 1, 15), 'c', 'Customer', 'regular', Decimal('100')),
            RecordedSale(2, date(2025, 4, 15), 'c', 'Customer', 'regular', Decimal('200')),
        ), 0)
        with patch('forecasting.beta_graph.valid_coverage', return_value={
                date(2025, 2, 1): SimpleNamespace(confirmed_zero=True)}):
            rows = graph._regular_monthly_series(snap)
        self.assertEqual([row['actual'] for row in rows], [100.0, 0.0, None, 200.0])

    def test_sixth_observation_with_zero_validation_does_not_erase_beta_model(self):
        rows = [{'date':f'2025-{i:02d}-01', 'actual':value}
                for i, value in enumerate([100,100,100,0,0,500],1)]
        result = graph._train(rows,'monthly')
        self.assertEqual(result['status'],'provisional')
        self.assertEqual(result['state']['name'],'local_level')
        self.assertEqual(result['selection'],'local_level_zero_validation_denominator')
        self.assertTrue(all(v['wape'] is None for v in result['validation'].values()))
        self.assertIsNotNone(graph._point(result,date(2025,7,1),'monthly'))

    def test_failed_nonzero_validation_is_not_mistaken_for_zero_sales(self):
        rows = [{'date':f'2025-{i:02d}-01', 'actual':100} for i in range(1,7)]
        failed = {'status':'fit_failed','validation':{'ar1':{'wape':None,'error':'fit failed'}}}
        with patch('forecasting.beta_graph.train',return_value=failed), \
                patch('forecasting.beta_graph.fit',side_effect=AssertionError('No known zero validation')):
            self.assertEqual(graph._train(rows,'monthly'),failed)


@override_settings(IS_PRODUCTION=True)
class ProductionDemoGuards(SimpleTestCase):
    def test_production_rejects_demo_fitting_before_database_access(self):
        with self.assertRaisesMessage(ValueError, 'unavailable in production'):
            graph.context('monthly','demo2026',date(2026,10,25))
        with self.assertRaisesMessage(CommandError, 'unavailable in production'):
            call_command('refresh_beta_graphs',scope='demo2026',stdout=StringIO())

    def test_regular_refresh_processes_only_real_sales(self):
        namespace='forecasting.management.commands.refresh_beta_graphs.refresh'
        with patch(namespace,return_value=(SimpleNamespace(pk=1),False)) as refresh:
            call_command('refresh_beta_graphs',stdout=StringIO())
        self.assertEqual(refresh.call_count,3)
        self.assertEqual({c.args[0] for c in refresh.call_args_list},set(graph.PERIODS))
        self.assertTrue(all(c.args[1]=='real' for c in refresh.call_args_list))


class BetaGraphTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(username='beta-admin', email='beta-admin@example.test', role='admin')
        self.staff = get_user_model().objects.create_user(username='beta-staff', email='beta-staff@example.test', role='staff')
        self.client.force_authenticate(self.admin)
        for month in range(1, 13):
            self.sale(date(2025, month, 15), 'regular', 100+month)
            self.sale(date(2025, month, 16), 'feeding', 50+month)
        for month in range(1, 13):
            self.sale(date(2026, month, 10), 'regular', 200+month, f'2026:{month:06d}')
            self.sale(date(2026, month, 11), 'feeding', 80+month, f'2026:{month+12:06d}')

    def sale(self, day, segment, amount, reference=None):
        txn = Transaction.objects.create(handled_by=self.admin, total_amount=amount,
            source_sales_segment=segment, source_reference=reference)
        Transaction.objects.filter(pk=txn.pk).update(created_at=timezone.make_aware(datetime.combine(day, datetime.min.time()))+timedelta(hours=12))
        return txn

    def refresh(self, period='monthly', scope='demo2026', as_of=date(2026,7,15)):
        with patch('forecasting.beta_graph.train', side_effect=fake_train), patch('forecasting.beta_graph._point', side_effect=fake_point):
            return graph.refresh(period, scope, as_of)

    def test_all_nine_views_have_three_series_and_the_exact_centered_windows(self):
        for period in graph.PERIODS:
            run, created = self.refresh(period)
            self.assertTrue(created)
            response = graph.report(period, 'demo2026', date(2026,7,15))
            self.assertEqual(response['status'], 'ready')
            self.assertNotIn('artifacts', response)
            self.assertTrue(response['is_demo'])
            for key, view in response['views'].items():
                points = view['points']
                radius = 2 if period == 'yearly' else 3
                self.assertEqual(len(points), 2*radius+1)
                self.assertTrue(points[radius]['is_current'])
                self.assertTrue(points[radius]['is_partial_period'])
                self.assertTrue(all(p['future_prediction'] is None for p in points[:radius]))
                self.assertTrue(all(p['past_prediction'] is None for p in points[radius:]))
                self.assertTrue(all(p['actual'] is None for p in points[radius+1:]))
                self.assertTrue(all(p['prediction_basis'] == 'fixed_origin_demo' for p in points))
                for score in view['evaluation'].values():
                    self.assertLessEqual(score['periods'], radius)
            for rows in zip(*(response['views'][key]['points'] for key in graph.VIEWS)):
                combined, regular, feeding = rows
                for series in ('past_prediction', 'future_prediction'):
                    if regular[series] is not None and feeding[series] is not None:
                        self.assertEqual(combined[series], regular[series]+feeding[series])
            self.assertFalse(GraphPrediction.objects.exists())  # Demo never creates real issued evidence.

    def test_dummy_or_real_2026_sales_cannot_enter_demo_training_and_dummy_changes_reuse_fits(self):
        self.sale(date(2026,2,1), 'regular', 999999, '2026:REAL:1')
        ctx = graph.context('monthly', 'demo2026', date(2026,7,15))
        for item in ctx['inputs'].values():
            self.assertTrue(all(row['date'] < '2026-01-01' for row in item['rows']))
            self.assertTrue(all(row['actual'] is None or row['actual'] < 1000 for row in item['rows']))
        first, _ = self.refresh()
        Transaction.objects.filter(source_reference='2026:000006').update(total_amount=9999)
        with patch('forecasting.beta_graph.train', side_effect=AssertionError('dummy data must not retrain')), \
                patch('forecasting.beta_graph._point', side_effect=fake_point):
            second, created = graph.refresh('monthly', 'demo2026', date(2026,7,15))
        self.assertTrue(created)
        before = first.findings['views']['regular']['points']
        after = second.findings['views']['regular']['points']
        self.assertEqual([p['past_prediction'] for p in before], [p['past_prediction'] for p in after])
        self.assertNotEqual([p['actual'] for p in before], [p['actual'] for p in after])
        real = graph.context('monthly', 'real', date(2026,7,15))
        self.assertFalse(any(pk in Transaction.objects.filter(source_reference__regex=r'^2026:[0-9]{6}$').values_list('pk', flat=True)
                             for pk, _, _, _ in real['sales']))

    def test_real_historical_fits_only_use_sales_before_the_target_and_future_uses_closed_data(self):
        self.sale(date(2026,5,1), 'regular', 10000, '2026:REAL:2')
        ctx = graph.context('monthly', 'real', date(2026,7,15))
        for (target, segment), item in ctx['inputs'].items():
            cutoff = min(target, date(2026,7,1))
            self.assertTrue(all(date.fromisoformat(row['date']) < cutoff for row in item['rows']))
        yearly = graph.context('yearly', 'real', date(2026,7,15))
        current = yearly['inputs'][(date(2026,1,1),'regular')]
        self.assertTrue(all(row['date'] < '2026-07-01' for row in current['rows']))
        historical = yearly['inputs'][(date(2025,1,1),'regular')]
        self.assertTrue(all(row['date'] < '2025-01-01' for row in historical['rows']))

    def test_regular_zero_review_is_used_only_while_its_source_revision_is_valid(self):
        month = date(2025, 2, 1)
        Transaction.objects.filter(source_sales_segment='regular', created_at__year=2025,
                                   created_at__month=2).delete()
        target = (date(2026, 1, 1), 'regular')
        def february(ctx, segment='regular'):
            return next(row['actual'] for row in ctx['inputs'][(target[0], segment)]['rows']
                        if row['date'] == month.isoformat())
        unknown = graph.context('monthly', 'real', date(2026, 1, 15))
        self.assertIsNone(february(unknown))
        snap = load_snapshot(date(2026, 1, 1))
        review = MonthlyCoverage.objects.create(month=month, source_revision=month_revision(snap, month),
            confirmed_zero=True, reviewed_by=self.admin, note='Isolated test: reconciled no regular invoices')
        reviewed = graph.context('monthly', 'real', date(2026, 1, 15))
        self.assertEqual(february(reviewed), 0.0)
        self.assertEqual(february(reviewed, 'feeding'), 52.0)
        self.assertNotEqual(unknown['inputs'][target]['signature'], reviewed['inputs'][target]['signature'])
        self.assertNotEqual(unknown['revision'], reviewed['revision'])
        # Even a feeding correction invalidates the exact-month reconciliation.
        # An old zero assertion must not survive a changed source ledger.
        self.sale(date(2025, 2, 20), 'feeding', 10, 'coverage-correction-test')
        corrected = graph.context('monthly', 'real', date(2026, 1, 15))
        self.assertIsNone(february(corrected))
        review.refresh_from_db()
        self.assertTrue(review.confirmed_zero)  # No attestation was altered.

    def test_partial_demo_boundary_week_is_not_scored_as_a_complete_week(self):
        self.sale(date(2026,1,1),'regular',50,'2026:009999')
        run,_=self.refresh('weekly','demo2026',date(2026,1,12))
        point=next(p for p in run.findings['views']['regular']['points'] if p['date']=='2025-12-29')
        self.assertEqual(point['actual'],50)
        self.assertTrue(point['is_partial_period'])
        # Only the following complete week, containing the Jan 10 dummy sale,
        # can contribute to a weekly score.
        self.assertEqual(run.findings['views']['regular']['evaluation']['fixed_origin_demo']['periods'],1)

    def test_sparse_beta_fit_does_not_require_six_observed_periods(self):
        rows=[{'date':'2025-11-01','actual':100},{'date':'2025-12-01','actual':120}]
        with patch('forecasting.beta_graph.fit',return_value={'trained_through':'2025-12-01'}) as fit:
            component=graph._train(rows,'monthly')
        self.assertIn('state',component)
        fit.assert_called_once_with(rows,'local_level',12)

    def test_get_never_fits_and_source_changes_withhold_old_graphs(self):
        with patch('forecasting.beta_graph.train', side_effect=AssertionError('GET cannot train')):
            response = self.client.get('/api/forecasting/graph/', {'scope':'demo2026','as_of':'2026-07-15'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'refresh_required')
        self.refresh()
        Transaction.objects.filter(source_reference='2026:000006').update(total_amount=999)
        with patch('forecasting.beta_graph.train', side_effect=AssertionError('GET cannot train')):
            response = self.client.get('/api/forecasting/graph/', {'scope':'demo2026','as_of':'2026-07-15'})
        self.assertEqual(response.data['status'], 'refresh_required')
        self.assertTrue(any(p['actual'] is not None for p in response.data['views']['regular']['points']))

    def test_future_predictions_are_saved_and_later_shown_as_original_predictions(self):
        initial = date(2026,7,15)
        with patch('django.utils.timezone.now', return_value=timezone.make_aware(datetime(2026,7,15,12))):
            first,_=self.refresh('monthly','real',initial)
        self.assertEqual(GraphPrediction.objects.count(),9)
        frozen = list(GraphPrediction.objects.values_list('id','value'))
        # A later implementation version must still show original issued values.
        GraphForecastRun.objects.filter(pk=first.pk).update(engine_version='centered-beta-sarima-v0')
        self.sale(date(2026,8,10),'regular',200,'2026:REAL:3')
        with patch('django.utils.timezone.now', return_value=timezone.make_aware(datetime(2026,9,15,12))):
            run, _ = self.refresh('monthly','real',date(2026,9,15))
        august = next(p for p in run.findings['views']['regular']['points'] if p['date']=='2026-08-01')
        self.assertEqual(august['past_prediction'],123)
        self.assertEqual(august['prediction_basis'],'issued_forecast')
        self.assertEqual(august['actual'],200)
        self.assertTrue(all(GraphPrediction.objects.get(pk=pk).value==value for pk,value in frozen))
        self.assertIn('issued_forecast',run.findings['views']['regular']['evaluation'])

    def test_replayed_combined_and_components_come_from_one_complete_issue(self):
        with patch('django.utils.timezone.now',return_value=timezone.make_aware(datetime(2026,7,15,12))):
            first,_=self.refresh('monthly','real',date(2026,7,15))
        # A later refresh could issue regular predictions while feeding fitting
        # fails. Replaying each view independently would yield 999+123 != 246.
        partial = GraphForecastRun.objects.create(engine_version=graph.VERSION,scope='real',period='monthly',
            as_of=date(2026,7,20),source_revision='partial',findings={})
        with patch('django.utils.timezone.now',return_value=timezone.make_aware(datetime(2026,7,20,12))):
            GraphPrediction.objects.create(run=partial,view='regular',target=date(2026,8,1),value=999)
        run,_=self.refresh('monthly','real',date(2026,9,15))
        points={v:next(p for p in run.findings['views'][v]['points'] if p['date']=='2026-08-01') for v in graph.VIEWS}
        self.assertEqual(points['regular']['past_prediction'],123)
        self.assertEqual(points['combined']['past_prediction'],
                         points['regular']['past_prediction']+points['feeding']['past_prediction'])
        self.assertTrue(all(p['prediction_basis']=='issued_forecast' for p in points.values()))
        # With no complete issued bundle, retain real component issues but do
        # not manufacture a Combined prediction that was never issued.
        GraphPrediction.objects.filter(run=first,target=date(2026,8,1)).delete()
        ctx=graph.context('monthly','real',date(2026,9,15))
        with patch('forecasting.beta_graph.train',side_effect=fake_train), \
                patch('forecasting.beta_graph._point',side_effect=fake_point):
            result=graph.build(ctx)
        regular=next(p for p in result['views']['regular']['points'] if p['date']=='2026-08-01')
        combined=next(p for p in result['views']['combined']['points'] if p['date']=='2026-08-01')
        self.assertEqual(regular['past_prediction'],999)
        self.assertIsNone(combined['past_prediction'])

    def test_invalidated_and_corrupt_artifacts_do_not_fit_on_get(self):
        run,_ = self.refresh()
        corrupted = deepcopy(run.findings)
        corrupted['views']['regular']['points'][0]['actual']=99999
        GraphForecastRun.objects.filter(pk=run.pk).update(findings=corrupted)
        with patch('forecasting.beta_graph.train',side_effect=AssertionError('no fit')):
            result=graph.report('monthly','demo2026',date(2026,7,15))
        self.assertEqual(result['status'],'refresh_required')
        self.assertNotEqual(result['views']['regular']['points'][0]['actual'],99999)

    def test_permissions_queries_and_legacy_contract_are_preserved(self):
        with override_settings(IS_PRODUCTION=True):
            self.assertEqual(self.client.get('/api/forecasting/graph/', {'scope':'demo2026'}).status_code,400)
            self.assertEqual(self.client.get('/api/forecasting/graph/', {'scope':'real'}).status_code,200)
        for values in ({'period':'daily'}, {'scope':'dummy'}, {'as_of':'invalid'},
                       {'as_of':'9999-01-01','scope':'demo2026'}, {'as_of':'2099-01-01'},
                       {'scope':'demo2026','as_of':'2025-01-01'}):
            self.assertEqual(self.client.get('/api/forecasting/graph/',values).status_code,400)
        legacy=self.client.get('/api/forecasting/chart/')
        self.assertIn('provisional_status',legacy.data['views']['regular'])
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get('/api/forecasting/graph/').status_code,403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/forecasting/graph/').status_code,401)

    def test_source_race_does_not_save_a_mixed_snapshot(self):
        original=graph.context('monthly','demo2026',date(2026,7,15))
        changed={**original,'revision':'changed'}
        with patch('forecasting.beta_graph.context',side_effect=[original,changed]), \
             patch('forecasting.beta_graph.train',side_effect=fake_train), \
             patch('forecasting.beta_graph._point',side_effect=fake_point):
            with self.assertRaisesMessage(ValueError,'Sales changed'):
                graph.refresh('monthly','demo2026',date(2026,7,15))
        self.assertFalse(GraphForecastRun.objects.exists())
