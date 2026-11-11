from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
import math
import numpy as np
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase
from django.utils import timezone
from config.test_support import APITestCase
from inventory.models import Category, Product
from .methods import forecast, eligibility, normalize_parameters, METHODS
from .demand_analysis import classify
from .evaluation import metrics, compare
from .analysis_workspace import prepare, build_analysis
from .analysis_views import ConfigurationSerializer
from .models import ForecastRefreshJob, ForecastAnalysisRun, QualitativeForecastRevision
from .provisional import digest
from .test_hierarchy import source_data


def dates(n): return [date(2023,1,2)+timedelta(weeks=i) for i in range(n)]


class ExpandedMathTests(SimpleTestCase):
    def test_sarima_is_the_only_operational_method(self):
        enabled = {key for key, spec in METHODS.items()
                   if spec['implementation_status'] == 'implemented'}
        self.assertEqual(enabled, {'sarima'})
        self.assertEqual(normalize_parameters('sarima'), {})
        retired = ('naive', 'sma', 'wma', 'ses', 'holt', 'holt_winters', 'seasonal_naive',
                   'trend_projection', 'linear_regression', 'multiple_linear_regression',
                   'correlation_regression', 'causal', 'decomposition', 'arima', 'croston',
                   'croston_sba', 'local_level', 'ar1', 'exponential_smoothing',
                   'sarima_100_100_12')
        for method in retired:
            with self.subTest(method=method), self.assertRaises(ValueError):
                normalize_parameters(method)
            with self.subTest(method=method), self.assertRaises(ValueError):
                forecast([10.] * 36, method, 1, 12)
        with self.assertRaises(ValueError):
            normalize_parameters('sarima', {'order': [1, 0, 0]})

    def test_sarima_fits_the_configured_season_and_returns_finite_forecasts(self):
        values = [100 + 2*i + 12*math.sin(2*math.pi*i/12) for i in range(60)]
        result = forecast(values, 'sarima', 6, 12)
        self.assertEqual(len(result), 6)
        self.assertTrue(all(math.isfinite(value) and value >= 0 for value in result))
        scaled = forecast([100*value for value in values], 'sarima', 6, 12)
        self.assertTrue(np.allclose(np.array(scaled)/100, result, atol=.01))

    def test_accuracy_including_zeros_is_explicit(self):
        score=metrics([{'actual':0,'predicted':2},{'actual':4,'predicted':2}])
        self.assertEqual(score['mae'],2)
        self.assertEqual(score['mse'],4)
        self.assertEqual(score['rmse'],2)
        self.assertEqual(score['wape_percent'],100)
        self.assertEqual(score['mape_percent'],50)
        self.assertEqual(score['mape_excluded_zero_actuals'],1)
        self.assertEqual(score['bias'],0)
        zeros=metrics([{'actual':0,'predicted':0}])
        self.assertIsNone(zeros['mape_percent']);self.assertIsNone(zeros['wape_percent'])

    def test_later_test_cannot_select_or_qualify_a_model(self):
        monthly_dates = [date(2022 + i//12, i%12+1, 1) for i in range(60)]
        original = [100 + 2*i + 12*math.sin(2*math.pi*i/12) for i in range(60)]
        altered = original[:-6] + [1000.] * 6
        with patch('forecasting.evaluation.forecast', side_effect=lambda values, method, horizon, season, parameters:
                   [float(values[-1])]*horizon):
            a = compare(original, monthly_dates, 'monthly', 6)
            b = compare(altered, monthly_dates, 'monthly', 6)
        self.assertEqual(a['selected_method'],b['selected_method'])
        self.assertEqual(a['selected_method'], 'sarima')
        self.assertEqual([row['method'] for row in a['methods']], ['sarima'])
        self.assertEqual([r['validation'] for r in a['methods']],[r['validation'] for r in b['methods']])
        self.assertNotEqual([r['test'] for r in a['methods']],[r['test'] for r in b['methods']])
        self.assertEqual(a['protocol']['horizon'],6)

    def test_gap_matched_comparisons_score_only_the_requested_distance_window(self):
        monthly_dates = [date(2022 + i//12, i%12+1, 1) for i in range(72)]
        values = [100 + 2*i + 12*math.sin(2*math.pi*i/12) for i in range(72)]
        with patch('forecasting.evaluation.forecast', side_effect=lambda values, method, horizon, season, parameters:
                   [float(values[-1])]*horizon):
            result = compare(values, monthly_dates, 'monthly', 6, lead_gap=4)
        self.assertEqual(result['protocol']['forecast_distance_start'],5)
        self.assertEqual(result['protocol']['forecast_distance_end'],10)
        self.assertEqual(result['protocol']['training_end'],monthly_dates[51].isoformat())
        self.assertEqual(result['protocol']['validation_start'],monthly_dates[56].isoformat())
        self.assertEqual(result['protocol']['test_start'],monthly_dates[66].isoformat())
        self.assertEqual(result['selected_method'], 'sarima')
        self.assertTrue(all(r['test']['periods']==6 for r in result['methods']))

    def test_independent_classification_and_missing_calendar_policy(self):
        stable=classify([10.]*30,dates(30),'weekly')
        self.assertIn('stable',stable['labels']);self.assertIsNone(stable['confidence'])
        intermittent=classify([0,0,0,10]*12,dates(48),'weekly')
        self.assertEqual(intermittent['primary'],'intermittent')
        lumpy=classify([0,0,1,0,0,100]*10,dates(60),'weekly')
        self.assertEqual(lumpy['primary'],'lumpy')
        erratic=classify([1,100]*20,dates(40),'weekly')
        self.assertIn('erratic',erratic['labels'])
        trend=classify(list(range(1,61)),dates(60),'weekly')
        self.assertIn('trending',trend['labels'])
        seasonal=classify([10,20,30,10,20,30,50]*12,dates(84),'daily')
        self.assertIn('seasonal',seasonal['labels'])
        cycle=classify([100+20*math.sin(2*math.pi*i/15) for i in range(90)],dates(90),'daily')
        self.assertIn('cyclical',cycle['labels'])
        self.assertEqual(classify([1,None]*20,dates(40),'weekly')['status'],'insufficient_data')
        self.assertEqual(classify([0]*40,dates(40),'weekly')['status'],'insufficient_data')


class ExpandedAPITests(APITestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_user(username='forecast-expanded',email='forecast@example.test',role='admin')
        self.staff=get_user_model().objects.create_user(username='forecast-expanded-staff',email='staff@example.test',role='staff')
        self.category=Category.objects.create(name='No sales category')
        self.product=Product.objects.create(name='Never sold',category=self.category,unit='L',shelf_life=7)
        self.client.force_authenticate(self.admin)
        self.config={'period':'weekly','metric':'demand','segment':'combined','scope':'item','scope_id':self.product.pk,
            'unit':'L','horizon_category':'short','horizon':8,'mode':'automatic','method':None,'parameters':{},'training_start':None,'training_end':None}

    def test_complete_catalog_is_read_only_and_admin_only(self):
        response=self.client.get('/api/forecasting/analysis/catalog/')
        self.assertEqual(response.status_code,200)
        self.assertTrue(any(p['id']==self.product.pk for p in response.data['products']))
        self.assertFalse(response.data['subcategory_support']['available'])
        self.assertEqual(response.data['accuracy_target']['below_percent'],8)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get('/api/forecasting/analysis/catalog/').status_code,403)
        self.assertEqual(self.client.post('/api/forecasting/analysis/runs/',self.config,format='json').status_code,403)
        self.assertEqual(self.client.post('/api/forecasting/analysis/expert/',{},format='json').status_code,403)

    def test_configuration_rejects_unsupported_horizons_methods_and_fields(self):
        for override in ({'method':'causal','mode':'manual'}, {'horizon':36,'horizon_category':'long'},
                         {'training_start':'2025-02-01','training_end':'2025-01-01'}, {'unknown':True}):
            serializer=ConfigurationSerializer(data={**self.config,**override})
            self.assertFalse(serializer.is_valid(),str(override))
        serializer=ConfigurationSerializer(data=self.config)
        self.assertTrue(serializer.is_valid(),serializer.errors)

    def test_analysis_uses_existing_job_and_persists_only_forecast_metadata(self):
        from .analysis_jobs import execute_analysis
        from contextlib import nullcontext
        source=source_data()
        config={**self.config,'scope':'overall','scope_id':None,'unit':'L','mode':'inspect'}
        with patch('forecasting.analysis_jobs.launch_after_commit'), \
                patch('forecasting.analysis_jobs.maintenance_lock', return_value=nullcontext(True)), \
                patch('forecasting.refresh_jobs.maintenance_lock', return_value=nullcontext(True)):
            response=self.client.post('/api/forecasting/analysis/runs/',config,format='json')
        self.assertEqual(response.status_code,202)
        run=ForecastAnalysisRun.objects.get(pk=response.data['id'])
        self.assertEqual(run.job.scope,'analysis')
        with patch('forecasting.analysis_jobs.load_source',return_value=source), \
                patch('forecasting.analysis_jobs.build_analysis',return_value={
                    'comparison':{'status':'inspected'},'series':{'forecast':[]}}):
            result=execute_analysis(run.job)
        self.assertEqual(result['status'],'succeeded')
        from contextlib import nullcontext
        with patch('forecasting.refresh_jobs.maintenance_lock', return_value=nullcontext(True)):
            detail=self.client.get(f'/api/forecasting/analysis/runs/{run.pk}/')
        self.assertEqual(detail.data['integrity'],'verified')
        self.assertEqual(detail.data['findings']['comparison']['status'],'inspected')
        self.product.refresh_from_db();self.assertEqual(self.product.name,'Never sold')

    def expert(self,**overrides):
        from .hierarchy import start
        return {'product_id':self.product.pk,'period':'weekly','segment':'combined','kind':'absolute',
            'targets':[{'date':start(timezone.localdate(),'weekly').isoformat(),'value':'12'}],
            'reason':'Client supplied estimate','assumptions':'Confirmed event order','expected_change':'Additional event demand',
            'event':'Client event',**overrides}

    def test_qualitative_revisions_are_attributed_versioned_and_do_not_change_stock(self):
        response=self.client.post('/api/forecasting/analysis/expert/',self.expert(),format='json')
        self.assertEqual(response.status_code,201,response.data)
        identity=response.data['id']
        revised=self.client.post(f'/api/forecasting/analysis/expert/{identity}/',self.expert(expected_version=1,reason='Updated client estimate'),format='json')
        self.assertEqual(revised.status_code,201,revised.data)
        self.assertEqual([r['version'] for r in revised.data['revisions']],[2,1])
        self.assertEqual(revised.data['revisions'][0]['created_by']['id'],self.admin.pk)
        conflict=self.client.post(f'/api/forecasting/analysis/expert/{identity}/',self.expert(expected_version=1),format='json')
        self.assertEqual(conflict.status_code,409)
        self.assertEqual(QualitativeForecastRevision.objects.count(),2)
        self.assertEqual(self.client.post('/api/forecasting/analysis/expert/',self.expert(kind='percent'),format='json').status_code,400)

    def test_percentage_delta_and_unit_checked_expert_baselines(self):
        from .hierarchy import start
        day=start(timezone.localdate(),'weekly').isoformat()
        job=ForecastRefreshJob.objects.create(scope='analysis',trigger='manual',status='succeeded',requested_by=self.admin)
        findings={'series':{'forecast':[{'date':day,'value':100.}]}}
        run=ForecastAnalysisRun.objects.create(job=job,configuration=self.config,engine_version='test',findings=findings,checksum=digest(findings))
        percent=self.client.post('/api/forecasting/analysis/expert/',self.expert(kind='percent',base_run_id=run.pk,targets=[{'date':day,'value':'10'}]),format='json')
        self.assertEqual(percent.status_code,201,percent.data)
        self.assertEqual(percent.data['revisions'][0]['targets'][0]['value'],110.)
        delta=self.client.post('/api/forecasting/analysis/expert/',self.expert(kind='delta',base_run_id=run.pk,targets=[{'date':day,'value':'-5'}]),format='json')
        self.assertEqual(delta.status_code,201,delta.data)
        self.assertEqual(delta.data['revisions'][0]['targets'][0]['value'],95.)
        run.configuration={**self.config,'unit':'unit'};run.save(update_fields=['configuration'])
        wrong=self.client.post('/api/forecasting/analysis/expert/',self.expert(kind='percent',base_run_id=run.pk),format='json')
        self.assertEqual(wrong.status_code,400)
        rounded=self.client.post('/api/forecasting/analysis/expert/',self.expert(targets=[{'date':day,'value':'1.005'}]),format='json')
        self.assertEqual(rounded.data['revisions'][0]['targets'][0]['value'],1.01)

    def test_default_hierarchy_contains_never_sold_products_and_empty_categories(self):
        from .hierarchy import build
        source=source_data()
        source['products'][999]={**source['products'][1],'id':999,'name':'Never sold fixture','category_id':999,'category_name':'Empty fixture category'}
        source['categories']=[{'id':1,'name':'Milk'},{'id':999,'name':'Empty fixture category'},{'id':1000,'name':'No products'}]
        with patch('forecasting.hierarchy.predict', side_effect=lambda values, method, horizon, season:
                   [next(float(v) for v in reversed(values) if v is not None)]*horizon):
            result=build(source,'weekly',as_of=source['latest_day'])
        for view in result['views'].values():
            product=next(i for i in view['items'] if i['id']==999)
            self.assertIsNone(product['demand']);self.assertIsNone(product['revenue'])
            self.assertEqual(len(view['categories']),3)
            self.assertEqual(next(c for c in view['categories'] if c['id']==999)['item_count'],1)

    def test_default_window_uses_recent_ledger_run_without_compressing_requested_gaps(self):
        source=source_data()
        older=source['earliest_day']-timedelta(weeks=60)
        source['coverage_days'].add(older)
        source['facts'].append({**source['facts'][0],'day':older})
        config={**self.config,'scope':'overall','scope_id':None,'unit':'unit'}
        recent=prepare(config,source)
        requested=prepare({**config,'training_start':older.isoformat()},source)
        self.assertEqual(recent['dates'][0],source['earliest_day'])
        self.assertGreater(len(requested['dates']),len(recent['dates']))
        self.assertTrue(any(v is None for v in requested['values']))

    def test_default_trailing_unknowns_become_lead_time_but_explicit_window_retains_them(self):
        source=source_data()
        config={**self.config,'scope':'overall','scope_id':None,'unit':'unit'}
        original=prepare(config,source)
        recent=date(2026,11,4)
        source['coverage_days'].add(recent)
        source['facts'].append({**source['facts'][0],'day':recent})
        source['latest_day']=recent
        actual=prepare(config,source)
        self.assertEqual(actual['dates'],original['dates'])
        self.assertEqual(actual['values'],original['values'])
        requested=prepare({**config,'training_end':'2026-11-01'},source)
        self.assertGreater(len(requested['dates']),len(actual['dates']))
        self.assertTrue(any(v is None for v in requested['values']))
        with patch('forecasting.analysis_workspace.accuracy_target',return_value={}):
            result=build_analysis({**config,'mode':'inspect','horizon':8},source)
        self.assertGreater(result['comparison']['protocol']['lead_gap'],0)

    def test_unexpected_worker_failures_do_not_expose_private_details(self):
        from .analysis_jobs import execute_analysis
        job=ForecastRefreshJob.objects.create(scope='analysis',trigger='manual',requested_by=self.admin)
        ForecastAnalysisRun.objects.create(job=job,configuration=self.config,engine_version='test')
        with patch('forecasting.analysis_jobs.load_source',side_effect=ValueError('private internal source details')):
            result=execute_analysis(job)
        self.assertEqual(result['status'],'failed')
        self.assertNotIn('private internal source details',result['results']['detail'])

    def test_missing_product_history_is_unavailable_not_zero(self):
        config={**self.config,'mode':'inspect'}
        source=source_data();source['products'][self.product.pk]={
            'id':self.product.pk,'name':'Never sold','unit':'L','category_id':self.category.pk,'category_name':self.category.name,'is_active':True}
        source['facts']=[f for f in source['facts'] if f['product_id']!=self.product.pk]
        with patch('forecasting.analysis_workspace.accuracy_target',return_value={}): result=build_analysis(config,source)
        self.assertEqual(result['observed_item_count'],0)
        self.assertTrue(all(p['value'] is None for p in result['series']['forecast']))
