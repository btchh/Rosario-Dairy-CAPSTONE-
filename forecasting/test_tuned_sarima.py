from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from .rebuilt import aggregate, measured, period_end, row
from .segmented_sarima import Model, specifications as previous_grid
from .tuned_sarima import (evaluate_period, fit, rank, select_plan, specifications,
                          target_status, validate_candidates)


class TunedSarimaTests(SimpleTestCase):
    def daily(self):
        dates = pd.date_range('2023-01-01', '2025-12-31')
        other = pd.Series(1000. + dates.dayofweek * 100, index=dates)
        feeding = pd.Series(np.where((dates >= '2024-11-01') & (dates.day == 15), 20000., 0.), index=dates)
        return other + feeding, {'feeding': feeding, 'other_sales': other}

    def model(self, window=91, transform='level'):
        return Model('daily', window, transform, (1, 0, 0), (1, 0, 0, 7))

    def previous(self):
        return {'approaches': {'existing': {'selection': {
            'models': {name: self.model().values() for name in ('total', 'feeding', 'other_sales')}}}}}

    @staticmethod
    def fake_fit(history, spec, steps):
        return np.repeat(float(history.iloc[-(spec.window or len(history)):].mean()), steps)

    def test_grid_retains_all_prior_candidates_and_uses_calendar_seasons(self):
        for period, lag in (('monthly', 12), ('weekly', 52)):
            grid = specifications(period)
            self.assertEqual(len(grid), len(set(grid)))
            self.assertTrue(set(previous_grid(period)[1]).issubset(grid))
            self.assertTrue(any(spec.transform == 'log_median' for spec in grid))
            self.assertTrue(any(spec.seasonal_order[1] == 1 for spec in grid))
            self.assertEqual({spec.seasonal_order[-1] for spec in grid}, {7, lag})
            self.assertTrue(all(any(spec.seasonal_order[:3]) for spec in grid))

    def test_joint_selection_optimizes_total_instead_of_individual_component_errors(self):
        dates = pd.date_range('2025-01-01', periods=6, freq='MS')
        targets = pd.Series(100., index=dates)
        def attempt(spec, actual, prediction):
            rows = [row(stamp, actual, stamp-pd.Timedelta(days=1), 'monthly',
                        [prediction, prediction, prediction]) for stamp in dates]
            return spec, rows, measured(rows)
        f, o = self.model(), self.model(365)
        attempts = {'total': [attempt(f, 100, 130)],
                    'feeding': [attempt(f, 50, 60), attempt(o, 50, 30)],
                    'other_sales': [attempt(f, 50, 60), attempt(o, 50, 35)]}
        plan, selected, _ = select_plan(attempts, targets, 'monthly', self.previous())
        self.assertEqual(plan, {'feeding': f, 'other_sales': o})
        self.assertEqual(selected['validation_metrics']['wape_percent'], 5.)
        self.assertGreater(attempts['other_sales'][1][2]['mae_pesos'], attempts['other_sales'][0][2]['mae_pesos'])

    def test_test_values_cannot_change_models_ranges_or_fixed_origin_predictions(self):
        daily, parts = self.daily()
        changed, changed_parts = daily.copy(), {name: value.copy() for name, value in parts.items()}
        changed.loc['2025-07-01':] *= 100
        for value in changed_parts.values():
            value.loc['2025-07-01':] *= 100
        grid = (self.model(), self.model(365, 'log_median'), Model('monthly', None, 'level', (1, 0, 0), (1, 0, 0, 12)))
        with patch('forecasting.tuned_sarima.specifications', return_value=grid), \
             patch('forecasting.tuned_sarima.fit', side_effect=self.fake_fit):
            first = evaluate_period(daily, parts, 'monthly', self.previous())
            second = evaluate_period(changed, changed_parts, 'monthly', self.previous())
        self.assertEqual(first['selection'], second['selection'])
        self.assertEqual(first['selection_signature'], second['selection_signature'])
        self.assertNotEqual(first['metrics'], second['metrics'])
        self.assertEqual([r['predicted'] for r in first['fixed_origin_rows']],
                         [r['predicted'] for r in second['fixed_origin_rows']])
        self.assertEqual(first['metrics']['rows'], 6)
        for item in first['rows']:
            self.assertLess(item['trained_through'], item['date'])

    def test_crossing_week_never_enters_validation(self):
        daily, parts = self.daily()
        with patch('forecasting.tuned_sarima.specifications', return_value=(self.model(),)), \
             patch('forecasting.tuned_sarima.fit', side_effect=self.fake_fit):
            result = evaluate_period(daily, parts, 'weekly', self.previous())
        self.assertEqual(result['selection']['validation_dates'][-1], '2025-06-23')
        self.assertEqual(result['rows'][0]['date'], '2025-07-07')
        self.assertEqual(result['rows'][0]['trained_through'], '2025-07-06')

    def test_candidate_cache_ignores_future_but_invalidates_changed_validation_history(self):
        daily, _ = self.daily()
        targets = aggregate(daily, 'monthly').loc['2025-01-01':'2025-06-01']
        with TemporaryDirectory() as directory, \
             patch('forecasting.tuned_sarima.specifications', return_value=(self.model(),)), \
             patch('forecasting.tuned_sarima.fit', side_effect=self.fake_fit) as fitted:
            first = validate_candidates(daily, targets, 'monthly', 'total', cache_dir=Path(directory))
            initial_calls = fitted.call_count
            future = daily.copy()
            future.loc['2025-07-01':] *= 100
            reused = validate_candidates(future, targets, 'monthly', 'total', cache_dir=Path(directory))
            self.assertEqual(fitted.call_count, initial_calls)
            self.assertEqual(first, reused)
            future.loc['2025-04-01'] *= 2
            validate_candidates(future, targets, 'monthly', 'total', cache_dir=Path(directory))
            self.assertGreater(fitted.call_count, initial_calls)

    def test_failed_predictions_disqualify_candidate_and_targets_are_strict(self):
        good = (self.model(), [], {'failed_rows': 0, 'mae_pesos': 100.})
        failed = (self.model(365), [], {'failed_rows': 1, 'mae_pesos': 0.})
        self.assertEqual(rank([failed, good]), [good])
        self.assertEqual(target_status({'rows': 6, 'failed_rows': 0, 'wape_percent': 24.99}), 'target_met')
        self.assertEqual(target_status({'rows': 6, 'failed_rows': 0, 'wape_percent': 25.}), 'target_not_met')
        self.assertEqual(target_status({'rows': 1, 'failed_rows': 0, 'wape_percent': 10.}), 'insufficient_evaluation')

    def test_real_seasonal_fit_preserves_zeros_and_log_mean_exceeds_log_median(self):
        dates = pd.date_range('2023-01-01', periods=120)
        history = pd.Series(1000. + dates.dayofweek*100 + np.random.default_rng(42).normal(0, 50, 120), index=dates)
        mean = fit(history, self.model(transform='log_mean'), 7)
        median = fit(history, self.model(transform='log_median'), 7)
        self.assertTrue(np.isfinite(mean).all())
        self.assertTrue((mean >= median - 1e-6).all())
        self.assertTrue((fit(history*0, self.model(), 7) == 0).all())
        with self.assertRaisesRegex(ValueError, '28 complete'):
            fit(history.iloc[:20], self.model(), 7)
