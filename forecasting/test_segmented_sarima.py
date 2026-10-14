from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from .rebuilt import period_end, row
from .segmented_sarima import Model, calendar_fit, evaluate, evaluate_period, specifications, walk
from .models import ForecastRun


class SegmentedSarimaTests(SimpleTestCase):
    def history(self):
        dates = pd.date_range('2023-01-01', '2025-12-31')
        other = pd.Series(1000. + dates.dayofweek * 100, index=dates)
        feeding = pd.Series(np.where((dates >= '2024-11-01') & (dates.day == 15), 20000., 0.), index=dates)
        return other + feeding, {'feeding': feeding, 'other_sales': other}

    @staticmethod
    def fake_walk(history, targets, spec, period, cutoff=None):
        rows = []
        for stamp, actual in targets.items():
            prefix = history[history.index < (cutoff if cutoff is not None else stamp)]
            point = float(prefix.iloc[-(spec.window or 365):].mean()) * ((period_end(stamp, period) - stamp).days + 1)
            item = row(stamp, actual, prefix.index[-1], period, [point, point, point])
            item['lower'] = item['upper'] = None
            rows.append(item)
        return rows

    def test_all_candidates_are_seasonal_and_existing_grid_is_retained(self):
        for period, lag in (('monthly', 12), ('weekly', 52)):
            original, expanded = specifications(period)
            self.assertTrue(set(original).issubset(expanded))
            self.assertEqual(len(expanded), len(set(expanded)))
            for spec in expanded:
                self.assertTrue(any(spec.seasonal_order[:3]))
                self.assertEqual(spec.seasonal_order[-1], 7 if spec.frequency == 'daily' else lag)

    def test_test_revenue_cannot_select_models_strategies_or_ranges(self):
        daily, parts = self.history()
        changed = daily.copy()
        changed.loc['2025-07-01':] *= 10
        changed_parts = {name: values.copy() for name, values in parts.items()}
        for values in changed_parts.values():
            values.loc['2025-07-01':] *= 10
        with patch('forecasting.segmented_sarima.walk', side_effect=self.fake_walk):
            first = evaluate_period(daily, parts, 'monthly')
            second = evaluate_period(changed, changed_parts, 'monthly')
        for engine in ('existing', 'segmented'):
            a, b = first['approaches'][engine], second['approaches'][engine]
            self.assertEqual(a['selection'], b['selection'])
            self.assertEqual(a['selection_signature'], b['selection_signature'])
            self.assertNotEqual(a['metrics'], b['metrics'])
            self.assertEqual([r['predicted'] for r in a['fixed_origin_rows']],
                             [r['predicted'] for r in b['fixed_origin_rows']])
            self.assertEqual(a['metrics']['rows'], 6)
            for index, item in enumerate(a['rows']):
                self.assertLess(item['trained_through'], item['date'])
                self.assertAlmostEqual(item['actual'], sum(rows[index]['actual'] for rows in a['component_rows'].values()), places=2)

    def test_week_crossing_test_boundary_is_not_used_for_selection(self):
        daily, parts = self.history()
        with patch('forecasting.segmented_sarima.walk', side_effect=self.fake_walk):
            result = evaluate_period(daily, parts, 'weekly')
        selected = result['approaches']['segmented']
        self.assertEqual(selected['selection']['validation_dates'][-1], '2025-06-23')
        self.assertEqual(selected['rows'][0]['date'], '2025-07-07')
        self.assertEqual(selected['metrics']['rows'], 25)

    def test_component_revenue_must_reconcile(self):
        daily, parts = self.history()
        parts['feeding'] *= 2
        with self.assertRaisesRegex(ValueError, 'reconcile'):
            evaluate_period(daily, parts, 'monthly')

    def test_calendar_model_needs_two_cycles_and_handles_real_seasonal_fit(self):
        dates = pd.date_range('2023-01-01', periods=36, freq='MS')
        history = pd.Series(1000. + 200 * np.sin(np.arange(36) * np.pi / 6)
                            + np.random.default_rng(42).normal(0, 50, 36), index=dates)
        for transform in ('level', 'log_mean'):
            spec = Model('monthly', None, transform, (1, 0, 0), (1, 0, 0, 12))
            with self.assertRaisesRegex(ValueError, '24 complete periods'):
                calendar_fit(history.iloc[:23], spec, 1)
            self.assertTrue(np.isfinite(calendar_fit(history, spec, 2)).all())
            self.assertTrue((calendar_fit(history * 0, spec, 2) == 0).all())

    def test_calendar_forecast_excludes_target_period_from_training(self):
        daily, _ = self.history()
        targets = pd.Series([1., 2.], index=pd.to_datetime(['2025-07-01', '2025-08-01']))
        seen = []
        def fit(history, spec, steps):
            seen.append((str(history.index[-1].date()), steps))
            return np.ones(steps)
        spec = Model('monthly', None, 'level', (1, 0, 0), (1, 0, 0, 12))
        with patch('forecasting.segmented_sarima.calendar_fit', side_effect=fit):
            walk(daily, targets, spec, 'monthly')
            walk(daily, targets, spec, 'monthly', pd.Timestamp('2025-07-01'))
        self.assertEqual(seen, [('2025-06-01', 1), ('2025-07-01', 1),
                                ('2025-06-01', 1), ('2025-06-01', 2)])

    def test_resume_preserves_completed_period_and_checkpoints_remaining_work(self):
        daily, parts = self.history()
        checkpoints = []
        monthly = {'completed_monthly_result': True}
        weekly = {'completed_weekly_result': True}
        with patch('forecasting.segmented_sarima.evaluate_period', return_value=weekly) as fit:
            result = evaluate(daily, parts, workers=1, completed={'monthly': monthly},
                              checkpoint=lambda values: checkpoints.append(dict(values)))
        fit.assert_called_once()
        self.assertEqual(fit.call_args.args[2], 'weekly')
        self.assertEqual(result['periods'], {'monthly': monthly, 'weekly': weekly})
        self.assertEqual(checkpoints, [result['periods']])


class SegmentedCommandTests(TestCase):
    def source(self, signature='a' * 64):
        return ({date(2025, 12, 31): Decimal('100')}, signature, [],
                {'feeding': {}, 'other_sales': {date(2025, 12, 31): Decimal('100')}})

    @patch('forecasting.management.commands.compare_segmented_sarima.evaluate')
    @patch('forecasting.management.commands.compare_segmented_sarima.source')
    def test_cached_experiment_and_exports_do_not_replace_production_version(self, source, evaluate):
        source.return_value = self.source()
        evaluate.return_value = {'periods': {}}
        with TemporaryDirectory() as directory:
            for _ in range(2):
                call_command('compare_segmented_sarima', output=Path(directory), stdout=StringIO())
            self.assertTrue((Path(directory) / 'comparison.json').is_file())
            self.assertTrue((Path(directory) / 'comparison.csv').is_file())
        evaluate.assert_called_once()
        from .contract import VERSION as production_version
        self.assertEqual(ForecastRun.objects.count(), 1)
        self.assertNotEqual(ForecastRun.objects.get().version, production_version)

    @patch('forecasting.management.commands.compare_segmented_sarima.evaluate', return_value={'periods': {}})
    @patch('forecasting.management.commands.compare_segmented_sarima.source')
    def test_source_change_discards_evaluation_and_releases_lock(self, source, evaluate):
        source.side_effect = [self.source(), self.source('b' * 64)]
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CommandError, 'Real source changed'):
                call_command('compare_segmented_sarima', output=Path(directory), stdout=StringIO())
            self.assertEqual(ForecastRun.objects.count(), 0)
            source.side_effect = None
            source.return_value = self.source()
            call_command('compare_segmented_sarima', output=Path(directory), stdout=StringIO())
            self.assertEqual(ForecastRun.objects.count(), 1)
