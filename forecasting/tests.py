from copy import deepcopy
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from sales.models import Transaction
from .contract import VERSION, Options, options, period_acceptance
from .data import source
from .models import ForecastRun, IssuedForecast
from .rebuilt import aggregate, evaluate_period, next_forecast, period_end, split_periods
from .sarima import Specification, candidates, forecast as sarima_forecast
from .serving import report


class SarimaEvaluationTests(SimpleTestCase):
    def daily(self):
        dates = pd.date_range('2023-01-01', '2025-12-31')
        return pd.Series(1000 + dates.month * 10, index=dates, dtype=float)

    def test_unrecorded_days_are_zero_and_2022_is_not_training_history(self):
        series = self.daily().iloc[[0, -1]]
        series.loc[pd.Timestamp('2022-03-21')] = 500_000
        months = aggregate(series, 'monthly')
        self.assertEqual(months.index[0], pd.Timestamp('2023-01-01'))
        self.assertEqual(months.loc['2023-02-01'], 0)
        self.assertEqual(months.loc['2023-01-01'], 1010)

    def test_2025_actuals_do_not_select_a_candidate(self):
        first = self.daily()
        changed = first.copy()
        changed.loc['2025-01-01':] *= 100

        def fake_forecast(history, spec, steps=1):
            point = np.repeat(float(history.mean()), steps)
            return point, point * .8, point * 1.2

        with patch('forecasting.rebuilt.forecast', side_effect=fake_forecast):
            original = evaluate_period(first, 'monthly')
            altered = evaluate_period(changed, 'monthly')
        self.assertEqual(original['method'], altered['method'])
        self.assertEqual(original['validation_metrics'], altered['validation_metrics'])
        self.assertNotEqual(original['metrics']['combined']['wape_percent'], altered['metrics']['combined']['wape_percent'])
        self.assertTrue(all(row['trained_through'] < row['date'] for row in original['rows']))
        self.assertEqual([row['predicted'] for row in original['fixed_origin_rows']],
                         [row['predicted'] for row in altered['fixed_origin_rows']])
        self.assertEqual({row['trained_through'] for row in original['fixed_origin_rows']}, {'2024-12-31'})
        self.assertEqual(len(original['rows']), 12)

    def test_new_year_crossing_week_cannot_influence_selection(self):
        original = self.daily()
        changed = original.copy()
        changed.loc['2025-01-01':'2025-01-05'] *= 1000

        def fake(history, spec, steps=1):
            prediction = np.repeat(float(history.iloc[-1]), steps)
            return prediction, prediction * .5, prediction * 1.5

        with patch('forecasting.rebuilt.forecast', side_effect=fake):
            first = evaluate_period(original, 'weekly')
            second = evaluate_period(changed, 'weekly')
        self.assertEqual(first['selection_signature'], second['selection_signature'])
        self.assertEqual(first['selection'], second['selection'])
        self.assertEqual(first['validation_end'], '2024-12-29')
        self.assertNotIn('2024-12-30', first['selection']['validation_dates'])
        self.assertEqual(first['rows'][0]['date'], '2025-01-06')
        self.assertEqual(first['rows'][0]['trained_through'], '2025-01-05')
        self.assertNotEqual(first['rows'][0]['predicted'], second['rows'][0]['predicted'])

    def test_calendar_aggregation_excludes_partial_start_and_end_weeks(self):
        weekly = aggregate(self.daily(), 'weekly')
        self.assertEqual(str(weekly.index[0].date()), '2023-01-02')
        self.assertEqual(str(period_end(weekly.index[-1], 'weekly').date()), '2025-12-28')
        validation, test = split_periods(weekly, 'weekly', Options())
        self.assertTrue((period_end(validation.index, 'weekly') < pd.Timestamp('2025-01-01')).all())
        self.assertEqual(len(test), 51)

    def test_a_new_sale_does_not_turn_unrecorded_live_months_into_zero_sales(self):
        daily = self.daily()
        daily.loc[pd.Timestamp('2026-10-01')] = 1000
        with self.assertRaisesRegex(ValueError, 'Unverified sales coverage for 2026-01'):
            aggregate(daily, 'monthly')

    def test_broader_grid_is_built_from_training_prefix(self):
        months = aggregate(self.daily(), 'monthly').loc[:'2024-06-01']
        specs, evidence = candidates(months, 'monthly')
        self.assertGreater(len(specs), 4)
        self.assertTrue(any(spec.order[1] == 1 for spec in specs))
        self.assertTrue(any(spec.order[2] == 1 for spec in specs))
        self.assertTrue(any(spec.window == 12 for spec in specs))
        self.assertFalse(any(spec.seasonal_order[-1] for spec in specs))
        self.assertEqual([item['periods'] for item in evidence['seasonal_hypotheses']], [12])
        self.assertEqual(evidence['training_observations'], 18)

    def test_weekly_grid_does_not_invent_short_calendar_seasons(self):
        weeks = aggregate(self.daily(), 'weekly').loc[:'2024-06-24']
        specs, evidence = candidates(weeks, 'weekly')
        self.assertFalse(any(spec.seasonal_order[-1] for spec in specs))
        self.assertEqual([item['periods'] for item in evidence['seasonal_hypotheses']], [52])

    def test_components_keep_all_revenue_in_total_error_and_baselines(self):
        total = self.daily()
        feeding = total * .8
        other = total - feeding
        spec = Specification((1, 0, 0))
        with patch('forecasting.rebuilt.candidates', return_value=((spec,), {})), \
             patch('forecasting.rebuilt.forecast', side_effect=lambda h, s, steps=1:
                   tuple(np.repeat(float(h.mean()), steps) for _ in range(3))):
            result = evaluate_period(total, 'monthly', components={'feeding': feeding, 'other_sales': other})
            broken = {'feeding': feeding, 'other_sales': other * 2}
            with self.assertRaisesRegex(ValueError, 'reconcile'):
                evaluate_period(total, 'monthly', components=broken)
        for index, item in enumerate(result['rows']):
            component_actual = sum(rows[index]['actual'] for rows in result['component_comparison'].values())
            self.assertAlmostEqual(item['actual'], component_actual, places=2)
        self.assertEqual(result['baselines']['seasonal_naive']['matched_rows'], 12)
        self.assertEqual(result['metrics']['combined']['attempted_rows'], 12)

    def test_partial_current_week_does_not_skip_to_untested_two_step_horizon(self):
        values = aggregate(self.daily(), 'weekly')
        with patch('forecasting.rebuilt.forecast', side_effect=AssertionError('Skipped horizon')):
            result = next_forecast({'total': values}, {'total': Specification((1, 0, 0))},
                                   'weekly', pd.Timestamp('2025-12-31'), 'test')
        self.assertEqual(result['status'], 'incomplete_current_period')

    def test_real_sarima_fit_and_zero_series_return_finite_currency(self):
        dates = pd.date_range('2023-01-02', periods=80, freq='W-MON')
        history = pd.Series(50000 + np.sin(np.arange(80) * np.pi / 2) * 5000, index=dates)
        point, low, high = sarima_forecast(history, Specification((1, 0, 0), (1, 0, 0, 4)), 3)
        self.assertTrue(np.isfinite(point).all())
        self.assertTrue((low <= high).all())
        self.assertTrue((point > 1000).all())
        zeros = sarima_forecast(history * 0, Specification((0, 1, 1)), 2)
        self.assertTrue((zeros[0] == 0).all())

    def test_target_needs_five_periods_and_no_more_than_30_percent_wape(self):
        self.assertEqual(period_acceptance({'rows':12, 'wape_percent':30}), 'accepted')
        self.assertEqual(period_acceptance({'rows':12, 'wape_percent':30.001}), 'rejected')
        self.assertEqual(period_acceptance({'rows':1, 'wape_percent':10}), 'insufficient_evaluation')
        self.assertEqual(period_acceptance({'rows':11, 'wape_percent':10, 'failed_rows':1}), 'model_unavailable')


class ForecastServingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='forecast-admin', email='forecast@example.com', role='admin')
        self.client = APIClient(HTTP_HOST='127.0.0.1')
        self.client.force_authenticate(self.user)
        self.end = timezone.localdate() - timedelta(days=1)
        self.point = {'status':'ready', 'method':'sarima_level_ar', 'date':str(self.end + timedelta(days=1)),
                      'end_date':str(self.end + timedelta(days=30)), 'trained_through':str(self.end),
                      'days':30, 'bulk':{'status':'not_used'},
                      'regular':{'predicted_revenue':100},
                      'combined':{'predicted_revenue':100, 'lower_bound':80, 'upper_bound':120}}
        self.result = {'selected_setup':'sarima', 'selection_trained_through':'2024-12-31',
                       'configuration':{'model':'SARIMA'}, 'data_provenance':{'source':'transactions'},
                       'setups':{'sarima':{'setup':'sarima', 'periods':{
                           'monthly':self.period(40, 12), 'yearly':self.period(10, 1),
                       }}}}
        self.run = ForecastRun.objects.create(
            scope='real', version=VERSION, configuration_signature=options().signature(),
            source_signature='a' * 64, data_end=self.end, result=self.result)

    def period(self, wape, rows):
        metric = {'rows':rows, 'wape_percent':wape, 'mape_percent':wape,
                  'mae_pesos':10, 'coverage_percent':50, 'interval_rows':rows}
        return {'method':'sarima_level_ar', 'metrics':{'combined':metric, 'regular':metric,
                'bulk':{}, 'combined_baselines':{}, 'regular_baselines':{}, 'bulk_baselines':{}},
                'rows':[{'date':'2025-01-01', 'actual':100, 'predicted':80}],
                'next_period':deepcopy(self.point)}

    def test_rejected_and_under_evaluated_forecasts_keep_holdout_visible(self):
        with patch('forecasting.serving.source', return_value=({self.end:Decimal('100')}, 'a' * 64, [])):
            monthly = report(period='monthly')
            yearly = report(period='yearly')
        self.assertEqual(monthly['status'], 'rejected')
        self.assertEqual(yearly['status'], 'insufficient_evaluation')
        self.assertEqual(monthly['forecast'], [])
        self.assertIn('40.00%', monthly['status_message'])
        self.assertIn('large sales swings', monthly['status_message'])
        self.assertIn('too few evaluated periods', yearly['status_message'])
        self.assertEqual(monthly['historical_comparison'][0]['actual'], 100)

    def test_only_original_issued_forecast_is_served_and_never_fitted_on_get(self):
        self.result['setups']['sarima']['periods']['monthly'] = self.period(20, 12)
        self.run.result = self.result
        self.run.save()
        issued = deepcopy(self.point)
        issued['combined']['predicted_revenue'] = 123
        IssuedForecast.objects.create(scope='real', version=VERSION,
            configuration_signature=options().signature(), setup='sarima', period='monthly',
            date=issued['date'], trained_through=self.end, payload=issued)
        with patch('forecasting.serving.source', return_value=({self.end:Decimal('100')}, 'a' * 64, [])), \
             patch('forecasting.rebuilt.forecast', side_effect=AssertionError('HTTP fitting')):
            payload = report(period='monthly')
        self.assertEqual(payload['status'], 'ready')
        self.assertEqual(payload['forecast'][0]['predicted_revenue'], 123)

    def test_changed_source_and_stale_sales_withhold_projection(self):
        with patch('forecasting.serving.source', return_value=({self.end:Decimal('100')}, 'b' * 64, [])):
            self.assertEqual(report()['status'], 'pending_update')
        self.result['setups']['sarima']['periods']['monthly'] = self.period(20, 12)
        self.run.result = self.result
        self.run.data_end = self.end - timedelta(days=2)
        self.run.save()
        with patch('forecasting.serving.source', return_value=({self.end:Decimal('100')}, 'a' * 64, [])):
            stale = report()
        self.assertEqual(stale['status'], 'stale_data')
        self.assertIn(str(self.end - timedelta(days=2)), stale['status_message'])
        self.assertIn('historical backtest', stale['status_message'])
        self.result['setups']['sarima']['periods']['monthly'] = self.period(40, 12)
        self.run.result = self.result
        self.run.save()
        with patch('forecasting.serving.source', return_value=({self.end:Decimal('100')}, 'a' * 64, [])):
            rejected_and_stale = report()
        self.assertEqual(rejected_and_stale['status'], 'stale_data')
        self.assertEqual(rejected_and_stale['quality_status'], 'rejected')

    def test_api_pdf_and_scope_validation(self):
        with patch('forecasting.serving.source', return_value=({self.end:Decimal('100')}, 'a' * 64, [])):
            response = self.client.get('/api/reports/preview/?type=sarima_forecast&period=monthly')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['data']['status'], 'rejected')
            pdf = self.client.get('/api/reports/export-pdf/?type=sarima_forecast')
            self.assertEqual(pdf.status_code, 200)
            self.assertTrue(b''.join(pdf.streaming_content).startswith(b'%PDF'))
            self.assertEqual(self.client.get('/api/reports/preview/?type=sarima_forecast&period=daily').status_code, 400)
            self.assertEqual(self.client.get('/api/forecasting/forecast/?period=daily').status_code, 400)
            self.assertEqual(self.client.get('/api/forecasting/forecast/?period=hourly').status_code, 400)
            self.assertEqual(self.client.get('/api/forecasting/forecast/?scope=simulation').status_code, 400)

    def test_current_period_estimates_are_separate_from_stale_sarima_forecast(self):
        today = date(2026, 10, 3)
        daily = {date(year, 1, 1) + timedelta(days=offset): Decimal('100')
                 for year in (2023, 2024, 2025)
                 for offset in range((date(year + 1, 1, 1) - date(year, 1, 1)).days)}
        self.result['setups']['sarima']['periods']['weekly'] = self.period(40, 51)
        self.run.result = self.result
        self.run.data_end = date(2025, 12, 31)
        self.run.save()
        with patch('forecasting.serving.timezone.localdate', return_value=today), \
             patch('forecasting.serving.source', return_value=(daily, 'a' * 64, [])):
            results = {period: self.client.get(
                f'/api/reports/preview/?type=sarima_forecast&period={period}').data['data']
                for period in ('weekly', 'monthly', 'yearly')}
        for period, result in results.items():
            self.assertEqual(result['status'], 'stale_data')
            self.assertEqual(result['forecast'], [])
            self.assertEqual(result['historical_comparison'], [])
            self.assertEqual(result['planning_projection']['point_kind'], 'historical_median')
            self.assertEqual(result['planning_projection']['sample_count'], 3)
        self.assertEqual(results['weekly']['planning_projection']['date'], '2026-09-28')
        self.assertEqual(results['weekly']['planning_projection']['predicted_revenue'], '700.00')
        self.assertEqual(results['monthly']['planning_projection']['date'], '2026-10-01')
        self.assertEqual(results['monthly']['planning_projection']['predicted_revenue'], '3100.00')
        self.assertEqual(results['yearly']['planning_projection']['date'], '2026-01-01')
        self.assertEqual(results['yearly']['planning_projection']['predicted_revenue'], '36500.00')

    def test_uncovered_years_do_not_become_zero_sales_estimates(self):
        from .historical import period_projection
        self.assertIsNone(period_projection(
            {date(2025, 12, 31): Decimal('90')}, 'monthly', date(2026, 10, 3)))

    def test_completed_non_voided_transactions_are_the_only_sales_source(self):
        stamp = timezone.make_aware(datetime.combine(self.end, time(12)))
        ordinary = Transaction.objects.create(handled_by=self.user, total_amount=90)
        voided = Transaction.objects.create(handled_by=self.user, total_amount=1000, is_voided=True)
        Transaction.objects.filter(pk__in=[ordinary.pk, voided.pk]).update(created_at=stamp)
        Transaction.objects.create(handled_by=self.user, total_amount=500)
        daily, _, _ = source()
        self.assertEqual(daily[self.end], Decimal('90'))
        self.assertNotIn(timezone.localdate(), daily)

    def test_component_changes_invalidate_snapshot_even_when_daily_total_is_unchanged(self):
        sale = Transaction.objects.create(handled_by=self.user, total_amount=100,
                                          source_customer_label='Milk Feeding-Las Pinas')
        Transaction.objects.filter(pk=sale.pk).update(created_at=timezone.make_aware(datetime.combine(self.end, time(12))))
        daily, first_signature, _, parts = source(include_components=True)
        self.assertEqual(parts['feeding'][self.end], daily[self.end])
        Transaction.objects.filter(pk=sale.pk).update(source_customer_label='Outlet Sales')
        unchanged, second_signature, _, parts = source(include_components=True)
        self.assertEqual(daily, unchanged)
        self.assertNotEqual(first_signature, second_signature)
        self.assertEqual(parts['other_sales'][self.end], daily[self.end])

    def test_misspelled_feeding_label_stays_in_feeding_component(self):
        sale = Transaction.objects.create(handled_by=self.user, total_amount=100,
                                          source_customer_label='MILK FEEDIN-DSWD')
        Transaction.objects.filter(pk=sale.pk).update(
            created_at=timezone.make_aware(datetime.combine(self.end, time(12))))
        daily, _, _, parts = source(include_components=True)
        self.assertEqual(parts['feeding'][self.end], daily[self.end])

    def test_issue_cannot_be_trained_on_target_day(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            IssuedForecast.objects.create(scope='real', version=VERSION,
                configuration_signature=options().signature(), setup='sarima', period='monthly',
                date=self.end, trained_through=self.end, payload={})


class ForecastCommandTests(SimpleTestCase):
    def test_private_export_is_opt_in(self):
        from types import SimpleNamespace
        from io import StringIO
        result = {'selected_setup':'sarima', 'setups':{'sarima':{'periods':{
            'monthly':{'method':'sarima_level_ar', 'validation_metrics':{'wape_percent':20},
                       'metrics':{'combined':{'wape_percent':25, 'rows':12}}, 'status':'accepted'},
        }}}}
        with patch('forecasting.management.commands.evaluate_forecast.refresh',
                   return_value=(SimpleNamespace(result=result), True)):
            call_command('evaluate_forecast', stdout=StringIO())
            with TemporaryDirectory() as directory:
                folder = Path(directory) / 'audit'
                call_command('evaluate_forecast', output=folder, stdout=StringIO())
                self.assertTrue((folder / 'real.json').is_file())
                self.assertTrue((folder / 'comparison.csv').is_file())
                self.assertTrue((folder / 'protocol.json').is_file())
