from unittest.mock import patch

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from .daily_sarima import DailyModel, evaluate_period, fit_path, range_radius, specifications, walk


class DailySarimaTests(SimpleTestCase):
    def daily(self):
        dates = pd.date_range('2023-01-01', '2025-12-31')
        return pd.Series(1000. + dates.dayofweek * 100, index=dates)

    def fake_path(self, history, spec, steps):
        return np.full(steps, history.iloc[-spec.window:].mean())

    def test_every_candidate_has_real_weekday_seasonality(self):
        self.assertTrue(all(spec.seasonal_order[3] == 7 for spec in specifications()))
        self.assertEqual({spec.transform for spec in specifications()}, {'level', 'log_mean'})

    def test_test_actuals_cannot_change_models_strategy_or_range(self):
        daily = self.daily()
        altered = daily.copy()
        altered.loc['2025-01-01':] *= 100
        with patch('forecasting.daily_sarima.fit_path', side_effect=self.fake_path):
            first = evaluate_period(daily, 'monthly', components={'feeding': daily*.2, 'other_sales': daily*.8})
            second = evaluate_period(altered, 'monthly', components={'feeding': altered*.2, 'other_sales': altered*.8})
        self.assertEqual(first['selection_signature'], second['selection_signature'])
        self.assertEqual(first['selection'], second['selection'])
        self.assertEqual([item['predicted'] for item in first['fixed_origin_rows']],
                         [item['predicted'] for item in second['fixed_origin_rows']])
        self.assertNotEqual(first['metrics'], second['metrics'])
        self.assertEqual(len(first['rows']), 12)
        self.assertTrue(all(item['trained_through'] < item['date'] for item in first['rows']))
        self.assertEqual(first['next_period']['days'], 31)

    def test_target_horizon_matches_calendar_and_crossing_week_is_excluded(self):
        calls = []
        def path(history, spec, steps):
            calls.append((history.index[-1], steps))
            return np.ones(steps)
        from .rebuilt import aggregate
        daily = self.daily()
        with patch('forecasting.daily_sarima.fit_path', side_effect=path):
            months = walk(daily, aggregate(daily, 'monthly').loc['2025-01-01':'2025-03-01'], DailyModel(91, 'level'), 'monthly')
            weekly = evaluate_period(daily, 'weekly')
        self.assertEqual([item['predicted'] for item in months], [31., 28., 31.])
        self.assertEqual(calls[:3], [(pd.Timestamp('2024-12-31'), 31), (pd.Timestamp('2025-01-31'), 28), (pd.Timestamp('2025-02-28'), 31)])
        self.assertEqual(weekly['validation_end'], '2024-12-29')
        self.assertNotIn('2024-12-30', weekly['selection']['validation_dates'])
        self.assertEqual(weekly['next_period']['status'], 'incomplete_current_period')

    def test_reconciliation_rejects_missing_or_extra_revenue(self):
        daily = self.daily()
        with self.assertRaisesRegex(ValueError, 'reconcile'):
            evaluate_period(daily, 'monthly', components={'feeding': daily*.3, 'other_sales': daily*.8})

    def test_range_uses_absolute_period_errors_and_requires_samples(self):
        rows = [{'actual': i, 'predicted': 0.} for i in range(1, 7)]
        self.assertEqual(range_radius(rows), 5.)
        with self.assertRaises(ValueError):
            range_radius(rows[:4])

    def test_actual_seasonal_fit_is_finite_in_currency_and_zeros_are_preserved(self):
        history = self.daily().iloc[:120] + np.random.default_rng(42).normal(0, 50, 120)
        for transform in ('level', 'log_mean'):
            path = fit_path(history, DailyModel(91, transform), 31)
            self.assertTrue(np.isfinite(path).all())
            self.assertTrue((path >= 0).all())
            self.assertGreater(path.sum(), 10000)
            self.assertTrue((fit_path(history*0, DailyModel(91, transform), 7) == 0).all())
        with self.assertRaises(ValueError):
            fit_path(history.iloc[:14], DailyModel(91, 'level'), 7)
