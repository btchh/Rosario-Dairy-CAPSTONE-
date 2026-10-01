from datetime import datetime,time,timedelta
from unittest.mock import patch
import numpy as np
import pandas as pd
from django.test import SimpleTestCase,TestCase,override_settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError,transaction
from django.utils import timezone
from rest_framework.test import APIClient
from .contract import Options,VERSION,options,period_acceptance
from .cutoffs import classify
from .calendar import bounds,bulk_estimate,complete_periods
from .engine import Predictor,Candidate,normalize
from .evaluation import evaluate,split_origin,predict_period,plan
from .metrics import score,comparisons
from .baselines import paths
from .models import ForecastRun,IssuedForecast
from .serving import report


class MonetaryExpectationTests(SimpleTestCase):
    def model(self,transform):
        model=object.__new__(Predictor)
        model.candidate=Candidate('test',transform)
        model.scale=100.;model.center=0.;model.spread=1.
        return model

    def test_expectation_includes_zero_clipping_and_transform_variance(self):
        # Independent normal half-moments: E[max(Z,0)] and E[max(Z,0)^2].
        self.assertAlmostEqual(self.model('level').monetary_mean([0.],[1.])[0],100/np.sqrt(2*np.pi))
        self.assertAlmostEqual(self.model('sqrt').monetary_mean([0.],[1.])[0],50.)
        from scipy.integrate import quad
        expected=quad(lambda z:(np.exp(z)-1)*np.exp(-z*z/2)/np.sqrt(2*np.pi),0,12)[0]*100
        self.assertAlmostEqual(self.model('log').monetary_mean([0.],[1.])[0],expected,places=6)

    def test_zero_variance_is_exact_decode_and_invalid_mean_is_rejected(self):
        for transform in ('level','sqrt','log'):
            model=self.model(transform)
            np.testing.assert_allclose(model.monetary_mean([-2.,0.,2.],[0.,0.,0.]),model.decode([-2.,0.,2.]))
        with self.assertRaises(ValueError):self.model('sqrt').monetary_mean([np.nan],[1.])

    def test_window_discards_old_observations_without_losing_calendar_history(self):
        index=pd.date_range('2025-01-01',periods=150)
        source=pd.Series(50_000+index.weekday*1000.,index=index)
        changed=source.copy();changed.iloc[:60]*=50
        candidate=Candidate('window_test','sqrt',(0,0,0),(1,0,0,7),90)
        a,b=Predictor(source,candidate),Predictor(changed,candidate)
        np.testing.assert_allclose(a.path(7)[0],b.path(7)[0])
        self.assertEqual(len(a.history),150)
        central,lo,hi=a.path(7,point_kind='central');mean,mean_lo,mean_hi=a.path(7,point_kind='mean')
        self.assertTrue((mean>=central).all())
        np.testing.assert_array_equal(lo,mean_lo);np.testing.assert_array_equal(hi,mean_hi)
        with self.assertRaises(ValueError):a.path(1,point_kind='invalid')

    @override_settings(FORECAST_SCOPE_OPTIONS={'real':{'regular_training_days':90,'regular_point_kind':'mean'}})
    def test_real_profile_changes_snapshot_identity(self):
        real=options('real')
        self.assertEqual(real.regular_training_days,90);self.assertEqual(real.regular_point_kind,'mean')
        self.assertNotEqual(real.signature(),Options().signature())
        with self.assertRaises(ValueError):options('simulation:1')
        for kwargs in ({'regular_training_days':0},{'regular_point_kind':'unknown'}):
            with self.assertRaises(ValueError):Options(**kwargs)


class CutoffTests(SimpleTestCase):
    def test_exact_past_positive_mad_formula_and_target_exclusion(self):
        source=pd.Series([10.,20.,30.]*10+[1_000_000.],index=pd.date_range('2025-01-01',periods=31))
        labels,regular=classify(source,60,Options())
        last=labels.iloc[-1]
        self.assertAlmostEqual(last['cutoff'],20+3*1.4826*10)
        self.assertFalse(last['fallback_used']);self.assertTrue(last['bulk'])
        self.assertTrue(pd.isna(regular.iloc[-1]))
        self.assertTrue(labels.iloc[-2]['fallback_used'])
        source.iloc[-1]=0
        changed,_=classify(source,60,Options())
        self.assertEqual(last['cutoff'],changed.iloc[-1]['cutoff'])

    def test_calendar_window_ignores_old_and_nonpositive_values(self):
        source=pd.Series([9_000_000.]*10+[10.,20.,30.]*10+[0.,np.nan,100.],index=pd.date_range('2025-01-01',periods=43))
        labels,_=classify(source,32,Options(multiplier=2))
        self.assertEqual(labels.iloc[-1]['positive_history_days'],30)
        self.assertAlmostEqual(labels.iloc[-1]['cutoff'],20+2*1.4826*10)

    def test_prefix_invariant_originals_and_missing_dates_unchanged(self):
        index=pd.date_range('2025-01-01',periods=100)
        source=pd.Series(50_000.,index=index);source.iloc[40]=0;source.iloc[41]=np.nan;source.iloc[42]=500_000
        original=source.copy();labels,regular=classify(source,60,Options())
        changed=source.copy();changed.iloc[70:]=10_000_000
        newer,new_regular=classify(changed,60,Options())
        pd.testing.assert_frame_equal(labels.iloc[:70],newer.iloc[:70])
        pd.testing.assert_series_equal(regular.iloc[:70],new_regular.iloc[:70])
        pd.testing.assert_series_equal(source,original)
        self.assertEqual(regular.iloc[40],0);self.assertTrue(pd.isna(regular.iloc[41]))

    def test_zero_mad_uses_exact_rule_and_equal_cutoff_is_regular(self):
        source=pd.Series(10.,index=pd.date_range('2025-01-01',periods=40))
        labels,_=classify(source,60,Options())
        self.assertEqual(labels.iloc[-1]['cutoff'],10)
        self.assertFalse(labels.iloc[-1]['bulk'])

    def test_fixed_comparison_and_config_validation(self):
        source=pd.Series([100_000.,100_001.],index=pd.date_range('2025-01-01',periods=2))
        labels,_=classify(source,None,Options())
        self.assertEqual(list(labels['bulk']),[False,True]);self.assertFalse(labels['fallback_used'].any())
        for kwargs in ({'windows':(0,)},{'multiplier':float('nan')},{'min_positive_days':0},{'monthly_lookback':4}):
            with self.assertRaises(ValueError):Options(**kwargs)


class CalendarRiskTests(SimpleTestCase):
    def test_recency_weights_keep_five_past_periods_and_ignore_future(self):
        source=pd.Series(10.,index=pd.date_range('2025-01-01','2025-02-28'))
        flags=pd.DataFrame({'bulk':False},index=source.index)
        for date,amount in zip(pd.date_range('2025-01-06',periods=5,freq='7D'),[100,200,300,400,500]):
            source.loc[date]=amount;flags.loc[date,'bulk']=True
        opts=Options(weekly_lookback=5,weekly_bulk_half_life=.5)
        original=source.copy();risk=bulk_estimate(source,flags,'weekly',pd.Timestamp('2025-02-10'),opts)
        self.assertEqual(risk['expected_revenue'],467.16);self.assertEqual(risk['expected_count'],1)
        self.assertEqual(risk['min_revenue'],100);self.assertEqual(risk['max_revenue'],500)
        self.assertEqual(risk['observed_periods'],5);self.assertEqual(risk['weighting'],'recency_weighted_mean')
        source.loc['2025-02-10':]=1_000_000
        self.assertEqual(risk,bulk_estimate(source,flags,'weekly',pd.Timestamp('2025-02-10'),opts))
        pd.testing.assert_series_equal(original.loc[:'2025-02-09'],source.loc[:'2025-02-09'])

    def test_yearly_uses_complete_previous_years_and_unknown_is_not_zero(self):
        source=pd.Series(10.,index=pd.date_range('2019-01-01','2024-12-31'))
        flags=pd.DataFrame({'bulk':False},index=source.index)
        for year in range(2019,2025):
            date=pd.Timestamp(year,7,3);source.loc[date]=(year-2018)*1000;flags.loc[date,'bulk']=True
        result=bulk_estimate(source,flags,'yearly',pd.Timestamp('2024-01-01'),Options())
        self.assertEqual(result['expected_revenue'],3000);self.assertEqual(result['observed_periods'],5)
        self.assertTrue(all(r['end_date']<'2024-01-01' for r in result['past_periods']))
        source.loc['2020-02-29']=np.nan
        self.assertEqual(bulk_estimate(source,flags,'yearly',pd.Timestamp('2024-01-01'),Options())['status'],'insufficient_history')
        start,end=bounds('2024-06-01','yearly');self.assertEqual((end-start).days+1,366)

    def source(self):
        source=pd.Series(10.,index=pd.date_range('2025-01-01','2025-12-31'))
        flags=pd.DataFrame({'bulk':False},index=source.index)
        for month in range(1,13):
            stamp=pd.Timestamp(2025,month,3);source.loc[stamp]=month*1000.;flags.loc[stamp,'bulk']=True
        return source,flags

    def test_only_previous_complete_same_type_periods_and_exact_extrema(self):
        source,labels=self.source();origin=pd.Timestamp('2025-06-01')
        first=bulk_estimate(source,labels,'monthly',origin,Options())
        self.assertEqual(first['expected_revenue'],3000);self.assertEqual(first['min_revenue'],1000)
        self.assertEqual(first['max_revenue'],5000);self.assertEqual(first['expected_count'],1)
        self.assertEqual(first['observed_periods'],5)
        changed=source.copy();changed.loc[origin:]=1_000_000
        self.assertEqual(first,bulk_estimate(changed,labels,'monthly',origin,Options()))
        self.assertTrue(all(r['end_date']<str(origin.date()) for r in first['past_periods']))

    def test_missing_period_is_not_zero_and_insufficient_has_no_estimate(self):
        source,labels=self.source();source.loc['2025-02-05']=np.nan
        result=bulk_estimate(source,labels,'monthly',pd.Timestamp('2025-06-01'),Options())
        self.assertEqual(result['status'],'insufficient_history');self.assertIsNone(result['expected_revenue'])
        self.assertEqual(result['observed_periods'],4)

    def test_zero_bulk_period_is_retained_and_leap_month_complete(self):
        source=pd.Series(0.,index=pd.date_range('2024-01-01','2024-06-30'))
        flags=pd.DataFrame({'bulk':False},index=source.index)
        result=bulk_estimate(source,flags,'monthly',pd.Timestamp('2024-07-01'),Options())
        self.assertEqual(result['expected_count'],0);self.assertEqual(result['expected_revenue'],0)
        self.assertEqual(result['observed_periods'],6)
        self.assertEqual(bounds('2024-02-10','monthly')[1],pd.Timestamp('2024-02-29'))

    def test_rolling_limit_is_previous_periods_not_lifetime_mean(self):
        source,labels=self.source()
        result=bulk_estimate(source,labels,'monthly',pd.Timestamp('2025-12-01'),Options())
        self.assertEqual(result['observed_periods'],6);self.assertEqual(result['expected_revenue'],8500)

    def test_currency_mean_uses_exact_cents_not_binary_rounding(self):
        from .calendar import money_mean,money_total
        values=[1191390.7,3123808.72,0.,1085775.56,437849.35,0.,1223722.4,
            1707742.06,1827326.51,212049.66,105018.8,393944.1]
        self.assertEqual(money_mean(values),942385.66)
        self.assertEqual(money_total([0.1,0.2,0.3]),0.6)

    def test_regular_and_bulk_components_do_not_double_count(self):
        risk={'status':'ready','expected_count':2.,'expected_revenue':200.,'min_revenue':100.,'max_revenue':300.}
        result=plan(70,35,105,risk,7)
        self.assertEqual(result['regular']['predicted_revenue'],50)
        self.assertEqual(result['combined']['predicted_revenue'],250)
        self.assertEqual(result['combined']['lower_bound'],125)
        self.assertEqual(result['combined']['upper_bound'],375)
        self.assertIn('not_calibrated',result['combined']['range_kind'])


class MetricTests(SimpleTestCase):
    def test_period_acceptance_uses_combined_wape_and_five_periods(self):
        self.assertEqual(period_acceptance({'rows':5,'wape_percent':30,'mape_percent':900}),'accepted')
        self.assertEqual(period_acceptance({'rows':5,'wape_percent':30.01,'mape_percent':1}),'rejected')
        self.assertEqual(period_acceptance({'rows':4,'wape_percent':1}),'insufficient_evaluation')
        self.assertEqual(period_acceptance({'rows':5,'wape_percent':None}),'insufficient_evaluation')
    def test_regular_percentage_metrics_use_positive_dates_only(self):
        result=score([{'actual':100,'predicted':110},{'actual':0,'predicted':100}],positive_only=True)
        self.assertEqual(result['mape_percent'],10);self.assertEqual(result['wape_percent'],10)
        self.assertEqual(result['rows'],1)

    def test_risk_coverage_and_absolute_error_include_zero_bulk(self):
        result=score([{'actual':0,'predicted':10,'lower':0,'upper':20},
            {'actual':100,'predicted':80,'lower':0,'upper':90}])
        self.assertEqual(result['coverage_percent'],50);self.assertEqual(result['mae_pesos'],15)

    def test_comparison_counts_and_small_period_conclusion(self):
        rows=[{'actual':100,'predicted':110,'baselines':{'median':100}},
              {'actual':100,'predicted':1000,'baselines':{'median':None}}]
        result=comparisons(rows,('median',),period=True)['median']
        self.assertEqual(result['shared_rows'],1);self.assertEqual(result['unavailable_rows'],1)
        self.assertEqual(result['model']['mape_percent'],10)
        self.assertEqual(result['status'],'too_few_to_conclude')

    def test_baselines_use_only_origin_week_for_long_horizons(self):
        source=pd.Series(np.arange(35.),index=pd.date_range('2025-01-01',periods=35))
        dates=pd.date_range(source.index[-1]+pd.Timedelta(days=1),periods=14)
        result=paths(source,dates)
        self.assertEqual(result['same_weekday_last_week'][:7],result['same_weekday_last_week'][7:])
        self.assertEqual(result['recent_median_28_days'][0],20.5)
        with self.assertRaises(ValueError):paths(source,[source.index[-1]])
        source.iloc[-7]=np.nan
        self.assertIsNone(paths(source,dates)['same_weekday_last_week'][0])


class LeakageTests(SimpleTestCase):
    def test_frozen_profile_prevents_earlier_months_becoming_test_targets(self):
        source=pd.Series(10.,index=pd.date_range('2025-01-01','2025-12-31'))
        self.assertEqual(split_origin(source,Options(profile_trained_through='2025-09-30')),pd.Timestamp('2025-10-01'))
    def source(self):
        index=pd.date_range('2024-01-01',periods=730)
        source=pd.Series(50_000+index.weekday*1000,index=index,dtype=float)
        source.iloc[::40]=500_000
        return source

    def fake_walk(self,history,future,candidate):
        self.assertLess(history.index[-1],future.index[0])
        value=float(history.dropna().mean())
        rows=[{'date':str(stamp.date()),'trained_through':str((stamp-pd.Timedelta(days=1)).date()),
            'actual':float(actual) if pd.notna(actual) else None,'predicted':value,'lower':value*.5,'upper':value*1.5} for stamp,actual in future.items()]
        return rows,type('State',(),{'refit_failures':0})()

    class FakePredictor:
        def __init__(self,history,candidate):self.history=history
        def path(self,days,point_kind=None):
            value=float(self.history.dropna().mean())
            return np.repeat(value,days),np.repeat(value*.5,days),np.repeat(value*1.5,days)

    def test_test_actuals_cannot_choose_cutoff_or_sarima(self):
        source=self.source();origin=split_origin(source)
        changed=source.copy();changed.loc[origin:]*=100
        candidate=(Candidate('sarima_test'),)
        with patch('forecasting.evaluation.walk',self.fake_walk),patch('forecasting.evaluation.Predictor',self.FakePredictor):
            first=evaluate(source,Options(),candidates=candidate)
            second=evaluate(changed,Options(),candidates=candidate)
        self.assertEqual(first['selection'],second['selection'])
        self.assertEqual(first['selected_setup'],second['selected_setup'])
        for name in first['setups']:
            self.assertEqual(first['setups'][name]['method'],second['setups'][name]['method'])
            self.assertEqual(first['setups'][name]['validation_rows'],second['setups'][name]['validation_rows'])
            self.assertLess(first['selection_trained_through'],first['setups'][name]['daily_rows'][0]['date'])

    def test_target_calendar_period_cannot_update_fit_or_bulk_estimate(self):
        source=self.source();start=pd.Timestamp('2025-10-01');end=pd.Timestamp('2025-10-31')
        changed=source.copy();changed.loc[start:]=1_000_000
        with patch('forecasting.evaluation.Predictor',self.FakePredictor):
            labels,regular=classify(source,60,Options())
            first=predict_period(source,labels,regular,start,end,Candidate('test'),Options())
            labels,regular=classify(changed,60,Options())
            second=predict_period(changed,labels,regular,start,end,Candidate('test'),Options())
        self.assertEqual(first,second)
        self.assertLess(first['trained_through'],first['date'])

    def test_split_origin_does_not_depend_on_future_missing_actuals(self):
        source=self.source();changed=source.copy();changed.iloc[-80:-1]=np.nan
        self.assertEqual(split_origin(source),split_origin(changed))


class EngineTests(SimpleTestCase):
    def test_native_intervals_missing_observations_and_money_units(self):
        index=pd.date_range('2025-01-01',periods=220)
        source=pd.Series(50_000+index.weekday*1000+np.random.default_rng(3).normal(0,100,len(index)),index=index)
        source.iloc[15]=np.nan
        model=Predictor(source,Candidate('sarima_units'))
        point,lower,upper=model.path(7)
        self.assertEqual(len(point),7);self.assertTrue(np.isfinite(upper).all())
        self.assertTrue((lower<=upper).all());self.assertTrue(40_000<point[0]<70_000)
        self.assertTrue(pd.isna(model.history.iloc[15]))
        model.observe(index[-1]+pd.Timedelta(days=1),np.nan)
        self.assertTrue(np.isfinite(model.path(1)[0]).all())

    def test_invalid_or_duplicate_sources_fail(self):
        for source in (pd.Series([-1.],index=pd.date_range('2025-01-01',periods=1)),
            pd.Series([1.,2.],index=pd.DatetimeIndex(['2025-01-01','2025-01-01']))):
            with self.assertRaises(ValueError):normalize(source)


class ServingTests(TestCase):
    def setUp(self):
        self.client=APIClient();User=get_user_model()
        self.staff=User.objects.create_user(username='hybrid-staff',email='hybrid-staff@example.com',role='staff')
        self.admin=User.objects.create_user(username='hybrid-admin',email='hybrid-admin@example.com',role='admin')
        self.end=timezone.localdate()-timedelta(days=1);self.daily={self.end:100.}
        self.point={'date':str(self.end+timedelta(days=1)),'trained_through':str(self.end),
            'predicted_revenue':100.,'lower_bound':80.,'upper_bound':120.,'interval_nominal_percent':80}
        self.setup={'setup':'dynamic_60','status':'rejected','method':'sarima_test',
            'regular_metrics':{'mape_percent':20.,'positive_rows':90},'zero_regular_days':0,
            'regular_baselines':{},'fallback_days':30,'daily_rows':[], 'next_day':self.point,'periods':{}}
        self.result={'selected_setup':'dynamic_60','selection_status':'selected','selection':[],
            'selection_trained_through':str(self.end-timedelta(days=90)), 'configuration':options().values(),
            'setups':{'dynamic_60':self.setup}}
        self.run=ForecastRun.objects.create(scope='real',version=VERSION,configuration_signature=options().signature(),
            source_signature='a'*64,data_end=self.end,result=self.result)
        self.point=self.monthly_fixture()

    def test_rejected_forecasts_are_withheld_even_if_an_issue_exists(self):
        IssuedForecast.objects.create(scope='real',version=VERSION,configuration_signature=options().signature(),
            setup='dynamic_60',period='monthly',date=self.point['date'],trained_through=self.end,payload=self.point)
        with patch('forecasting.serving.source',return_value=(self.daily,'a'*64,[])):
            payload=report(period='monthly')
        self.assertEqual(payload['status'],'rejected');self.assertEqual(payload['forecast'],[])
        self.assertNotIn('experimental_forecast',payload)

    def test_accepted_snapshot_uses_original_issued_point_without_fitting(self):
        from copy import deepcopy
        self.point=self.monthly_fixture(wape=20)
        original=deepcopy(self.point)
        original['regular']['predicted_revenue']-=10
        original['bulk']['expected_revenue']+=20
        original['combined']['predicted_revenue']+=10
        IssuedForecast.objects.create(scope='real',version=VERSION,configuration_signature=options().signature(),
            setup='dynamic_60',period='monthly',date=self.point['date'],trained_through=self.end,payload=original)
        with patch('forecasting.serving.source',return_value=(self.daily,'a'*64,[])),patch('forecasting.evaluation.evaluate',side_effect=AssertionError('HTTP fitting')):
            payload=report(period='monthly')
        self.assertEqual(payload['status'],'ready');self.assertEqual(payload['forecast'][0]['predicted_revenue'],original['combined']['predicted_revenue'])
        self.assertEqual(payload['bulk']['expected_revenue'],original['bulk']['expected_revenue'])
        self.assertEqual(payload['regular']['predicted_revenue'],original['regular']['predicted_revenue'])
        self.assertEqual(payload['bulk']['as_of'],original['trained_through'])
        self.assertNotIn('daily',payload['available_periods'])
        self.assertNotIn('regular_baselines',payload['regular'])

    def test_changed_or_stale_sales_do_not_serve_current_projections(self):
        with patch('forecasting.serving.source',return_value=(self.daily,'b'*64,[])):
            self.assertEqual(report(period='monthly')['status'],'pending_update')
        self.run.data_end=self.end-timedelta(days=2);self.run.save()
        with patch('forecasting.serving.source',return_value=(self.daily,'a'*64,[])):
            self.assertEqual(report(period='monthly')['status'],'stale_data')

    def test_authentication_and_real_only_query_validation(self):
        self.assertEqual(self.client.get('/api/forecasting/forecast/').status_code,401)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get('/api/forecasting/forecast/?scope=simulation:1').status_code,400)
        self.assertEqual(self.client.get('/api/forecasting/forecast/?period=daily').status_code,400)
        self.assertEqual(self.client.get('/api/forecasting/forecast/?scope=simulation:0').status_code,400)

    def test_daily_is_removed_and_yearly_reports_insufficient_history(self):
        from .calendar import next_period
        from .evaluation import period_scores
        start,end=next_period(pd.Timestamp(self.end),'yearly')
        point={'date':str(start.date()),'end_date':str(end.date()),'trained_through':str(self.end),
            'method':'sarima_test','days':(end-start).days+1,'status':'insufficient_history',
            'bulk':{'status':'insufficient_history','expected_revenue':None,'min_revenue':None,'max_revenue':None,'observed_periods':0},
            'regular':None,'combined':None}
        self.setup['periods']['yearly']={'rows':[],'metrics':period_scores([]),'next_period':point,'status':'insufficient_evaluation'}
        self.run.result=self.result;self.run.save();self.client.force_authenticate(self.staff)
        with patch('forecasting.serving.source',return_value=(self.daily,'a'*64,[])):
            with self.assertRaises(ValueError):report(period='daily')
            response=self.client.get('/api/forecasting/forecast/?period=yearly')
            self.assertEqual(response.status_code,200);self.assertEqual(response.data['status'],'insufficient_history')
            self.assertEqual(response.data['forecast'],[]);self.assertNotIn('daily',response.data['available_periods'])
            preview=self.client.get('/api/reports/preview/?type=sarima_forecast&period=yearly')
            self.assertEqual(preview.status_code,200);self.assertEqual(preview.data['data']['period'],'yearly')
            pdf=self.client.get('/api/reports/export-pdf/?type=sarima_forecast&period=yearly')
            self.assertEqual(pdf.status_code,200);self.assertTrue(b''.join(pdf.streaming_content).startswith(b'%PDF'))

    def test_failed_period_is_withheld_despite_accepted_flag_and_existing_issue(self):
        self.setup['periods']['monthly']['status']='accepted';self.run.result=self.result;self.run.save()
        IssuedForecast.objects.create(scope='real',version=VERSION,configuration_signature=options().signature(),
            setup='dynamic_60',period='monthly',date=self.point['date'],trained_through=self.end,payload=self.point)
        with patch('forecasting.serving.source',return_value=(self.daily,'a'*64,[])):
            payload=report(period='monthly')
        self.assertEqual(payload['status'],'rejected');self.assertEqual(payload['forecast'],[])

    def test_database_rejects_issue_using_its_target_day(self):
        with self.assertRaises(IntegrityError),transaction.atomic():
            IssuedForecast.objects.create(scope='real',version=VERSION,configuration_signature=options().signature(),
                setup='dynamic_60',period='daily',date=self.end,trained_through=self.end,payload={})

    def test_live_completed_net_and_void_totals_are_read_without_mutation(self):
        from sales.models import Transaction
        from .data import source
        stamp=timezone.make_aware(datetime.combine(self.end,time(12)))
        normal=Transaction.objects.create(handled_by=self.staff,subtotal=100,discount_amount=10,total_amount=90)
        void=Transaction.objects.create(handled_by=self.staff,total_amount=1000,is_voided=True)
        Transaction.objects.filter(pk__in=[normal.pk,void.pk]).update(created_at=stamp)
        Transaction.objects.create(handled_by=self.staff,total_amount=500)
        daily,_,_=source()
        self.assertEqual(daily[self.end],90);self.assertNotIn(timezone.localdate(),daily)
        normal.refresh_from_db();self.assertEqual(normal.total_amount,90)

    def monthly_fixture(self,wape=40):
        from .calendar import next_period
        from .evaluation import period_scores
        start,end=next_period(pd.Timestamp(self.end),'monthly')
        days=(end-start).days+1
        bulk={'status':'ready','expected_count':1.,'min_count':0,'max_count':2,
            'expected_revenue':2000.,'min_revenue':1000.,'max_revenue':3000.,
            'observed_periods':6,'required_periods':5,'past_periods':[]}
        point={'date':str(start.date()),'end_date':str(end.date()),'trained_through':str(self.end),
            'days':days,'method':'sarima_test','model':{'training_days':180,'point_kind':'monetary_mean'},
            'bulk':bulk,**plan(days*100.,days*50.,days*150.,bulk,days)}
        rows=[]
        for i in range(5):
            stamp=start-pd.DateOffset(months=i+2);_,stop=bounds(stamp,'monthly')
            actual=point['combined']['predicted_revenue']/(1+wape/100)
            rows.append({**point,'date':str(stamp.date()),'end_date':str(stop.date()),
                'trained_through':str((stamp-pd.Timedelta(days=1)).date()),
                'actual':actual,'actual_regular_revenue':actual-2000,'actual_bulk_revenue':2000,'actual_bulk_count':1})
        metrics=period_scores(rows)
        self.setup['periods']['monthly']={'rows':rows,'metrics':metrics,'next_period':point,
            'status':'accepted' if wape<=30 else 'rejected'}
        self.run.result=self.result;self.run.save()
        return point

    def test_rejected_monthly_report_keeps_risk_but_withholds_combined_and_exports_pdf(self):
        self.monthly_fixture();self.client.force_authenticate(self.staff)
        with patch('forecasting.serving.source',return_value=(self.daily,'a'*64,[])):
            response=self.client.get('/api/reports/preview/?type=sarima_forecast')
            self.assertEqual(response.status_code,200)
            payload=response.data['data']
            self.assertEqual(payload['forecast'],[]);self.assertEqual(payload['combined'],{})
            self.assertNotIn('predicted_revenue',payload['regular'])
            self.assertEqual(payload['bulk']['expected_revenue'],2000)
            pdf=self.client.get('/api/reports/export-pdf/?type=sarima_forecast')
            self.assertEqual(pdf.status_code,200);self.assertTrue(b''.join(pdf.streaming_content).startswith(b'%PDF'))

    def test_accepted_period_reports_separate_lines_and_additive_risk_bounds(self):
        point=self.monthly_fixture(wape=20)
        IssuedForecast.objects.create(scope='real',version=VERSION,configuration_signature=options().signature(),
            setup='dynamic_60',period='monthly',date=point['date'],trained_through=self.end,payload=point)
        with patch('forecasting.serving.source',return_value=(self.daily,'a'*64,[])):
            payload=report(period='monthly')
        self.assertEqual(payload['status'],'ready')
        self.assertEqual(payload['combined']['predicted_revenue'],payload['regular']['predicted_revenue']+payload['bulk']['expected_revenue'])
        self.assertEqual(payload['combined']['lower_bound'],payload['regular']['lower_bound']+payload['bulk']['min_revenue'])
        self.assertEqual(payload['combined']['upper_bound'],payload['regular']['upper_bound']+payload['bulk']['max_revenue'])

    def test_refresh_preserves_original_issue_after_sales_change(self):
        from copy import deepcopy
        from .jobs import refresh
        self.point=self.monthly_fixture(wape=20)
        original=deepcopy(self.point);original['combined']['predicted_revenue']-=10;original['regular']['predicted_revenue']-=10
        issued=IssuedForecast.objects.create(scope='real',version=VERSION,configuration_signature=options().signature(),
            setup='dynamic_60',period='monthly',date=self.point['date'],trained_through=self.end,payload=original)
        result=deepcopy(self.result);result['data_end']=str(self.end)
        result['setups']['dynamic_60']['periods']['monthly']['next_period']['combined']['predicted_revenue']=999.
        with patch('forecasting.jobs.source',return_value=(self.daily,'b'*64,[])),patch('forecasting.evaluation.evaluate',return_value=result):
            run,_=refresh()
        issued.refresh_from_db()
        self.assertEqual(issued.payload['combined']['predicted_revenue'],original['combined']['predicted_revenue'])
        self.assertEqual(run.result['setups']['dynamic_60']['periods']['monthly']['next_period']['combined']['predicted_revenue'],original['combined']['predicted_revenue'])
