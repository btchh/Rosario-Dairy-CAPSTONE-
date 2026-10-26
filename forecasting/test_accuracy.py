from copy import deepcopy
from datetime import date
from io import StringIO
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from .accuracy import compare_errors, error_summary, monthly_accuracy
from .learning import digest, shift_month
from .models import MonthlyModelRun, GraphPrediction


class ErrorEvidenceTests(SimpleTestCase):
    def test_bias_can_cancel_while_errors_remain_large(self):
        result = error_summary([{'actual': 100, 'predicted': 90}, {'actual': 100, 'predicted': 110}])
        self.assertEqual(result['bias'], 0)
        self.assertEqual(result['signed_bias_percent'], 0)
        self.assertEqual(result['wape_percent'], 10)
        self.assertEqual(result['rmse'], 10)
        self.assertEqual(result['error_stddev'], 10)
        self.assertEqual(result['max_absolute_error'], 10)

    def test_zero_actuals_keep_currency_error_but_have_no_percentage_error(self):
        result = error_summary([{'actual': 0, 'predicted': 10}])
        self.assertIsNone(result['wape_percent'])
        self.assertIsNone(result['signed_bias_percent'])
        self.assertEqual(result['mae'], 10)
        self.assertEqual(result['error_stddev'], 0)
        self.assertIsNone(error_summary([])['rmse'])

    def test_invalid_values_are_rejected(self):
        for value in (float('nan'), float('inf'), -1):
            for key in ('actual', 'predicted'):
                with self.assertRaises(ValueError):
                    error_summary([{'actual': 100, 'predicted': 100, key: value}])

    def test_skill_is_relative_error_reduction_and_can_be_negative(self):
        model = [{'month': '2025-01-01', 'actual': 100, 'predicted': 110}]
        baseline = [{**model[0], 'predicted': 120}]
        self.assertEqual(compare_errors(model, baseline)['absolute_error_reduction_percent'], 50)
        self.assertEqual(compare_errors(baseline, model)['absolute_error_reduction_percent'], -100)
        self.assertIsNone(compare_errors(model, [{**model[0], 'predicted': 100}])['absolute_error_reduction_percent'])
        for other in ([], [{**baseline[0], 'actual': 101}], [{**baseline[0], 'month': '2025-02-01'}]):
            with self.assertRaises(ValueError):
                compare_errors(model, other)

    def test_audit_rejects_mismatched_or_leaking_saved_comparisons(self):
        rows = [{'month': shift_month(date(2023, 1, 1), i).isoformat(), 'revenue': 100} for i in range(36)]
        folds = [{'month': r['month'], 'actual': 100, 'predicted': 110,
                  'trained_through': shift_month(date.fromisoformat(r['month']), -1).isoformat()}
                 for r in rows[-12:]]
        artifact = {'name': 'sarima_test'}
        findings = {'artifact': artifact, 'artifact_sha256': digest(artifact),
                    'selected_candidate': 'sarima_test', 'selected_baseline': 'previous_month',
                    'candidates': {'sarima_test': {'test_rows': folds},
                                   'previous_month': {'test_rows': deepcopy(folds)}}}
        run = SimpleNamespace(pk=1, status='research_candidate', findings=findings)
        with patch('forecasting.accuracy.valid_coverage', return_value={}), \
                patch('forecasting.accuracy.coverage_rows', return_value=rows), \
                patch('forecasting.accuracy.MonthlyModelRun.objects') as manager, \
                patch('forecasting.accuracy.quality_gate', return_value={'eligible': False}), \
                patch('forecasting.accuracy.is_sarima_artifact', return_value=True):
            manager.filter.return_value.first.return_value = run
            snapshot = SimpleNamespace(revision='source', as_of=date(2026, 10, 1))
            result = monthly_accuracy(snapshot)
            self.assertEqual(result['status'], 'retrospective_only')
            self.assertEqual(result['months_since_training'], 9)
            for key, value in [('actual', 999), ('trained_through', folds[0]['month']),
                               ('month', folds[1]['month']), ('predicted', float('nan'))]:
                original = folds[0][key]
                folds[0][key] = value
                self.assertEqual(monthly_accuracy(snapshot)['status'], 'invalid_comparison')
                folds[0][key] = original
            folds.pop()
            self.assertEqual(monthly_accuracy(snapshot)['status'], 'invalid_comparison')


class AccuracyCommandTests(TestCase):
    def test_empty_database_audit_never_fits_or_creates_forecasts(self):
        output = StringIO()
        with patch('statsmodels.tsa.statespace.sarimax.SARIMAX.fit', side_effect=AssertionError('audit must not train')):
            call_command('audit_forecast_accuracy', stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(result['fits_performed'], 0)
        self.assertEqual(result['regular_monthly']['status'], 'refresh_required')
        self.assertIsNone(result['regular_monthly']['comparison'])
        self.assertFalse(MonthlyModelRun.objects.exists())
        self.assertFalse(GraphPrediction.objects.exists())
