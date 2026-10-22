from datetime import date, datetime
from decimal import Decimal
from io import StringIO
import json
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from forecasting.data import RecordedSale, SalesSnapshot, load_snapshot
from forecasting.learning import (CANDIDATES, ENGINE_VERSION, digest, fit_model, month_end,
                                  monthly_rows, predict_model, shift_month, train, walk_forward)
from forecasting.lifecycle import (activate_model, attest_coverage, quality_gate, refresh_models,
                                   valid_coverage, prospective_evidence)
from forecasting.models import (MonthlyCoverage, MonthlyDeployment, MonthlyForecastScore,
                                MonthlyIssuedForecast, MonthlyModelRun, MonthlyPromotion)
from forecasting.serving import models_report, report
from forecasting.seasonal import SARIMA_CONFIGS
from sales.models import Transaction

ZONE = ZoneInfo('Asia/Manila')


def stamp(day):
    return datetime(day.year, day.month, day.day, 9, tzinfo=ZONE)


def months(count=36):
    return [{'month': shift_month(date(2023, 1, 1), i).isoformat(),
             'revenue': round(1000 + i * 7 + (i % 5) * 12, 2)} for i in range(count)]


class MonthlyLearningTests(SimpleTestCase):
    def test_artifact_checksum_survives_jsonb_signed_zero_normalization(self):
        self.assertEqual(digest({'params': [-0.0, {'value': 0.0}]}),
                         digest({'params': [0.0, {'value': -0.0}]}))

    def test_every_fitted_state_round_trips_as_finite_json(self):
        for name in CANDIDATES:
            with self.subTest(candidate=name):
                fitted = fit_model(name, months())
                loaded = json.loads(json.dumps(fitted, allow_nan=False))
                self.assertEqual(predict_model(fitted), predict_model(loaded))
                self.assertGreaterEqual(predict_model(fitted), 0)
                self.assertEqual(loaded['target_month'], '2026-01-01')

    def test_sarima_saved_parameters_reproduce_forecast_without_optimization(self):
        state = fit_model('sarima_010_100_12', months())
        self.assertEqual(state['family'], 'SARIMA')
        self.assertEqual(state['seasonal_order'], [1, 0, 0, 12])
        self.assertTrue(state['converged'])
        with patch('forecasting.seasonal.SARIMAX.fit', side_effect=AssertionError('must not refit')):
            self.assertEqual(predict_model(state), round(state['fitted_next_value'], 2))
        with self.assertRaisesRegex(ValueError, 'eighteen'):
            fit_model('sarima_010_100_12', months(17))

    def test_sarima_rejects_nonconvergence_after_two_bounded_attempts(self):
        fitted = Mock(mle_retvals={'converged': False}, params=[0.1, 1.0])
        model = Mock(); model.fit.return_value = fitted
        with patch('forecasting.seasonal._model', return_value=model):
            with self.assertRaisesRegex(ValueError, 'failed to converge'):
                fit_model('sarima_010_100_12', months())
        self.assertEqual(model.fit.call_count, 2)
        for call in model.fit.call_args_list:
            self.assertEqual(call.kwargs['maxiter'], 150)

    def test_baseline_can_win_comparison_but_cannot_replace_primary_sarima(self):
        def folds(name, rows, start, end):
            return [{'month': r['month'], 'actual': r['revenue'],
                     'predicted': r['revenue'] + (10 if name in SARIMA_CONFIGS else 0)}
                    for r in rows[start:end]]
        with patch('forecasting.learning.walk_forward', side_effect=folds):
            result = train(months())
        self.assertIn(result['selected_candidate'], SARIMA_CONFIGS)
        self.assertEqual(result['baseline_test']['wape_percent'], 0)
        self.assertFalse(result['retrospective_gate']['no_worse_than_baseline'])

    def test_failed_sarima_never_silently_falls_back_to_a_working_baseline(self):
        def fit(name, rows):
            if name in SARIMA_CONFIGS:
                raise ValueError('SARIMA failed convergence')
            return fit_model(name, rows)
        with patch('forecasting.learning.fit_model', side_effect=fit):
            result = train(months())
        self.assertEqual(result['status'], 'no_valid_candidates')
        self.assertNotIn('artifact', result)
        self.assertIn('validation', result['candidates']['previous_month'])

    def test_failed_final_sarima_refit_is_retained_as_failure(self):
        def fit(name, rows):
            if name in SARIMA_CONFIGS and len(rows) == 36:
                raise ValueError('Final SARIMA fit failed')
            return fit_model(name, rows)
        with patch('forecasting.learning.fit_model', side_effect=fit):
            result = train(months())
        self.assertEqual(result['status'], 'selected_model_failed')
        self.assertEqual(result['failure_phase'], 'final_refit')
        self.assertNotIn('artifact', result)

    def test_test_outcomes_cannot_choose_the_model(self):
        original = months(); result = train(original)
        changed = [dict(r) for r in original]
        for row in changed[-12:]: row['revenue'] *= 10
        later = train(changed)
        self.assertEqual(result['selected_candidate'], later['selected_candidate'])
        self.assertEqual(result['selected_baseline'], later['selected_baseline'])
        self.assertEqual(result['validation_end'], '2024-12-01')
        self.assertNotEqual(result['selected_test'], later['selected_test'])
        self.assertNotEqual(result['artifact_sha256'], later['artifact_sha256'])

    def test_walk_forward_never_fits_the_target_or_future_actuals(self):
        rows = months()
        with patch('forecasting.learning.fit_model', wraps=fit_model) as fitting:
            folds = walk_forward('sarima_010_100_12', rows, 24, 36)
        self.assertEqual(len(fitting.call_args_list), 12)
        for index, (call, fold) in enumerate(zip(fitting.call_args_list, folds)):
            prefix = call.args[1]
            self.assertEqual(len(prefix), 24 + index)
            self.assertLess(prefix[-1]['month'], fold['month'])
        rows[30]['revenue'] *= 10
        changed = walk_forward('sarima_010_100_12', rows, 24, 36)
        self.assertEqual(folds[:6], changed[:6])
        self.assertEqual(folds[6]['predicted'], changed[6]['predicted'])
        self.assertNotEqual(folds[7]['predicted'], changed[7]['predicted'])

    def test_windows_advance_with_completed_data_and_reject_gaps(self):
        before, after = train(months()), train(months(37))
        self.assertEqual(before['test_start'], '2025-01-01')
        self.assertEqual(after['test_start'], '2025-02-01')
        missing = months(37); del missing[10]
        with self.assertRaisesRegex(ValueError, 'missing months'):
            train(missing)
        self.assertEqual(train(months(29))['status'], 'insufficient_history')
        self.assertEqual(train(months(35))['required_months'], 36)

    def test_zero_history_does_not_fabricate_accuracy(self):
        rows = months()
        for row in rows: row['revenue'] = 0
        result = train(rows)
        self.assertEqual(result['status'], 'no_valid_candidates')

    def test_monthly_series_excludes_unclassified_feeding_partial_and_missing_months(self):
        sales = tuple(RecordedSale(i, date(2025, m, 15), 'buyer', 'Shop', segment, Decimal(amount))
                      for i, (m, segment, amount) in enumerate([
                          (1, 'regular', '100'), (2, 'feeding', '1000'),
                          (3, 'unclassified', '999'), (4, 'regular', '200'), (5, 'regular', '300')]))
        source = SalesSnapshot(date(2025, 5, 20), 'test', sales, 0)
        self.assertEqual(monthly_rows(source), [{'month': '2025-04-01', 'revenue': 200.0}])
        zeros = monthly_rows(source, [date(2025, 2, 1), date(2025, 3, 1)])
        self.assertEqual([row['revenue'] for row in zeros], [100, 0, 0, 200])


class MonthlyLifecycleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username='model-admin', email='model@example.test', role='admin')
        for row in months():
            day = date.fromisoformat(row['month']).replace(day=15)
            sale = Transaction.objects.create(handled_by=self.user, total_amount=str(row['revenue']),
                source_sales_segment='regular', source_reference=f'{day.year}:MONTH:{day.month}')
            Transaction.objects.filter(pk=sale.pk).update(created_at=stamp(day))
        self.client = APIClient(); self.client.force_authenticate(self.user)

    def at(self, day):
        return patch('django.utils.timezone.now', return_value=stamp(day))

    def test_fit_is_versioned_and_unchanged_or_partial_month_data_does_not_retrain(self):
        with self.at(date(2026, 1, 1)):
            first, result = refresh_models()
            self.assertTrue(result['created'])
            artifact = json.loads(json.dumps(first.findings))
            with patch('forecasting.lifecycle.train', side_effect=AssertionError('must reuse')):
                repeat, result = refresh_models()
            self.assertEqual(first.pk, repeat.pk)
            self.assertFalse(result['created'])
        with self.at(date(2026, 1, 22)):
            sale = Transaction.objects.create(handled_by=self.user, total_amount=2000, source_sales_segment='regular')
            with patch('forecasting.lifecycle.train', side_effect=AssertionError('partial month must not train')):
                repeat, result = refresh_models()
            self.assertEqual(first.pk, repeat.pk)
        with self.at(date(2026, 2, 1)):
            second, _ = refresh_models()
        self.assertNotEqual(second.pk, first.pk)
        self.assertEqual(second.trained_through, date(2026, 1, 31))
        first.refresh_from_db(); self.assertEqual(first.findings, artifact)

    def test_actual_correction_creates_a_new_run_without_overwriting_an_issued_prediction(self):
        with self.at(date(2026, 1, 1)):
            first, result = refresh_models()
            self.assertTrue(result['issued'])
            issued = MonthlyIssuedForecast.objects.get()
            value, model_id = issued.value, issued.model_run_id
            sale = Transaction.objects.get(source_reference='2025:MONTH:12')
            Transaction.objects.filter(pk=sale.pk).update(total_amount=1500)
            corrected, result = refresh_models()
            self.assertNotEqual(first.pk, corrected.pk)
            self.assertFalse(result['issued'])
            issued.refresh_from_db()
            self.assertEqual((issued.value, issued.model_run_id), (value, model_id))

    def test_no_issue_is_backdated_or_created_from_stale_data(self):
        with self.at(date(2026, 10, 22)):
            run, result = refresh_models()
            self.assertFalse(result['issued'])
            self.assertIn('stale_training_data', result['quality_gate']['reasons'])
            refresh_models(date(2026, 1, 1))
            self.assertFalse(MonthlyIssuedForecast.objects.exists())
            data = report()
            self.assertEqual(data['status'], 'stale_data')
            self.assertEqual(data['forecast'], [])
            self.assertEqual(models_report()['research_forecast']['date'], '2026-01-01')

    def test_no_promotion_from_retrospective_error_alone(self):
        with self.at(date(2026, 1, 1)):
            run, result = refresh_models()
            self.assertFalse(result['promoted'])
            self.assertIn('needs_12_prospective_reviewed_months', result['quality_gate']['reasons'])
            self.assertFalse(MonthlyDeployment.objects.exists())
            with self.assertRaisesRegex(ValueError, 'Activation withheld'):
                activate_model(run.pk)
            self.assertEqual(report()['forecast'], [])

    def test_model_and_coverage_changes_during_fit_abort_save(self):
        with self.at(date(2026, 1, 1)):
            original = load_snapshot(date(2026, 1, 1))
            sale = Transaction.objects.get(source_reference='2025:MONTH:12')
            Transaction.objects.filter(pk=sale.pk).update(total_amount=1550)
            changed = load_snapshot(date(2026, 1, 1))
            with patch('forecasting.lifecycle._snapshot', side_effect=[original, changed]):
                with self.assertRaisesRegex(ValueError, 'changed during training'):
                    refresh_models()
            self.assertFalse(MonthlyModelRun.objects.exists())

    def test_source_coverage_expires_after_correction_and_unknown_months_need_review(self):
        with self.at(date(2026, 1, 1)):
            reviewed, created = attest_coverage(date(2025, 12, 1), self.user.username, 'Reconciled original monthly ledger')
            self.assertTrue(created)
            source = load_snapshot(date(2026, 1, 1))
            self.assertIn(reviewed.month, valid_coverage(source))
            Transaction.objects.filter(source_reference='2025:MONTH:12').update(total_amount=1111)
            self.assertNotIn(reviewed.month, valid_coverage(load_snapshot(date(2026, 1, 1))))
            unknown = Transaction.objects.create(handled_by=self.user, source_sales_segment='unclassified', total_amount=20)
            Transaction.objects.filter(pk=unknown.pk).update(created_at=stamp(date(2025, 11, 15)))
            with self.assertRaisesRegex(ValueError, 'unclassified'):
                attest_coverage(date(2025, 11, 1), self.user.username, 'Not yet resolved')
            Transaction.objects.filter(source_reference='2025:MONTH:10').delete()
            with self.assertRaisesRegex(ValueError, 'confirm-zero'):
                attest_coverage(date(2025, 10, 1), self.user.username, 'No regular sales')
            zero, _ = attest_coverage(date(2025, 10, 1), self.user.username, 'Verified business closure', True)
            self.assertTrue(zero.confirmed_zero)

    def test_issued_scores_revise_without_destroying_old_actuals(self):
        with self.at(date(2026, 1, 1)):
            run, _ = refresh_models()
            issued = MonthlyIssuedForecast.objects.get()
        with self.at(date(2026, 1, 15)):
            actual = Transaction.objects.create(handled_by=self.user, source_sales_segment='regular', total_amount=1300)
        with self.at(date(2026, 2, 1)):
            _, result = refresh_models()
            self.assertEqual(result['new_scores'], 1)
            first = MonthlyForecastScore.objects.get(issued=issued)
            self.assertEqual(first.actual, 1300)
            self.assertFalse(first.coverage_verified)
            attest_coverage(date(2026, 1, 1), self.user.username, 'January original ledger checked')
            _, result = refresh_models()
            self.assertEqual(result['new_scores'], 1)
            Transaction.objects.filter(pk=actual.pk).update(total_amount=1200)
            refresh_models()
            self.assertEqual(issued.scores.count(), 3)
            first.refresh_from_db(); self.assertEqual(first.actual, 1300)
            evidence = prospective_evidence(load_snapshot(date(2026, 2, 1)),
                                              valid_coverage(load_snapshot(date(2026, 2, 1))))
            self.assertEqual(evidence['metrics']['periods'], 0)

    def test_model_api_is_admin_only_and_never_trains_on_get(self):
        with self.at(date(2026, 1, 1)):
            refresh_models()
            with patch('forecasting.lifecycle.train', side_effect=AssertionError('GET must not train')):
                response = self.client.get('/api/forecasting/models/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['status'], 'research_only')
            self.assertFalse(response.data['research_forecast_is_live'])
            self.assertEqual(report(period='weekly')['status'], 'unsupported_horizon')
        staff = get_user_model().objects.create_user(username='model-staff', role='staff')
        for user, expected in ((staff, 403), (None, 401)):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get('/api/forecasting/models/').status_code, expected)

    def test_commands_support_repeatable_maintenance_and_safe_arguments(self):
        with self.at(date(2026, 1, 1)):
            out = StringIO()
            call_command('watch_forecast_models', once=True, stdout=out)
            call_command('refresh_forecast_models', stdout=out)
            self.assertEqual(MonthlyModelRun.objects.count(), 1)
            with self.assertRaises(CommandError):
                call_command('watch_forecast_models', once=True, interval=1, stdout=out)
            with self.assertRaises(CommandError):
                call_command('refresh_forecast_models', as_of=date(2026, 2, 1), stdout=out)

    def seed_prospective_history(self):
        # Constant, genuinely predictable synthetic test history. The real
        # database never receives these fabricated acceptance fixtures.
        Transaction.objects.all().update(total_amount=1000)
        for i in range(12):
            day = shift_month(date(2022, 1, 1), i).replace(day=15)
            sale = Transaction.objects.create(handled_by=self.user, total_amount=1000, source_sales_segment='regular')
            Transaction.objects.filter(pk=sale.pk).update(created_at=stamp(day))
        for i in range(12):
            month = shift_month(date(2025, 1, 1), i)
            previous = shift_month(month, -1)
            artifact = fit_model('sarima_010_100_12', [
                {'month': shift_month(date(2022, 1, 1), n).isoformat(), 'revenue': 1000}
                for n in range(36 + i)])
            with self.at(month):
                run = MonthlyModelRun.objects.create(
                    engine_version=ENGINE_VERSION, source_revision=digest(month.isoformat()), coverage_revision=digest([]),
                    cutoff=month, trained_through=month_end(previous), status='research_candidate',
                    findings={'status': 'research_candidate', 'artifact': artifact,
                              'selected_candidate': artifact['name'],
                              'artifact_sha256': digest(artifact), 'next_month': {'date': month.isoformat()}})
                MonthlyIssuedForecast.objects.create(model_run=run, engine_version=ENGINE_VERSION,
                    target_month=month, value=1000, lower_bound=900, upper_bound=1100, baseline_value=1000)
        with self.at(date(2026, 1, 1)):
            for i in range(12):
                attest_coverage(shift_month(date(2025, 1, 1), i), self.user.username, 'Original monthly sources reconciled')

    def test_prospective_reviewed_evidence_promotes_and_serves_only_regular_monthly(self):
        self.seed_prospective_history()
        with self.at(date(2026, 1, 1)):
            run, result = refresh_models()
            self.assertTrue(result['promoted'])
            self.assertTrue(result['issued'])
            self.assertEqual(result['quality_gate']['prospective']['metrics']['periods'], 12)
            self.assertEqual(MonthlyDeployment.objects.get().model_run_id, run.pk)
            self.assertEqual(MonthlyPromotion.objects.count(), 1)
            data = report()
            self.assertEqual(data['status'], 'ready', {
                'gate': data.get('quality_gate'), 'model_id': data.get('model_id'),
                'issues': list(MonthlyIssuedForecast.objects.values('model_run_id', 'target_month'))})
            self.assertEqual(len(data['forecast']), 1)
            self.assertEqual(data['forecast'][0]['predicted_revenue'], Decimal('1000'))
            self.assertEqual(data['bulk']['status'], 'not_modelled')
            self.assertEqual(data['combined'], {})
            _, changed = activate_model(run.pk)
            self.assertFalse(changed)
            # Reject a model's fitted-state corruption, even with good scores.
            run.findings['artifact']['value'] = 999
            MonthlyModelRun.objects.filter(pk=run.pk).update(findings=run.findings)
            self.assertEqual(report()['forecast'], [])

    def test_bad_prospective_error_missing_coverage_or_late_issues_block_promotion(self):
        self.seed_prospective_history()
        with self.at(date(2026, 1, 1)):
            run, _ = refresh_models()
            source = load_snapshot(date(2026, 1, 1))
            coverage = valid_coverage(source)
            MonthlyIssuedForecast.objects.filter(target_month__year=2025).update(value=1200, upper_bound=1300)
            gate = quality_gate(run, source, coverage)
            self.assertFalse(gate['eligible'])
            self.assertIn('prospective_error_not_below_15_percent', gate['reasons'])
            MonthlyIssuedForecast.objects.filter(target_month__year=2025).update(value=1000)
            issue = MonthlyIssuedForecast.objects.get(target_month=date(2025, 12, 1))
            MonthlyIssuedForecast.objects.filter(pk=issue.pk).update(issued_at=stamp(date(2025, 12, 2)))
            gate = quality_gate(run, source, coverage)
            self.assertIn('needs_12_prospective_reviewed_months', gate['reasons'])
            MonthlyCoverage.objects.filter(month=date(2025, 10, 1)).delete()
            gate = quality_gate(run, source, valid_coverage(source))
            self.assertIn('unverified_ledger_coverage', gate['reasons'])

    def test_checksum_valid_baseline_cannot_pass_as_primary_sarima(self):
        with self.at(date(2026, 1, 1)):
            run, _ = refresh_models()
            artifact = fit_model('previous_month', months())
            run.findings.update(artifact=artifact, artifact_sha256=digest(artifact),
                                selected_candidate='previous_month')
            gate = quality_gate(run, load_snapshot(date(2026, 1, 1)), {})
            self.assertIn('primary_model_must_be_sarima', gate['reasons'])
