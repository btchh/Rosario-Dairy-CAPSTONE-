"""Prospective chronology, reviewed coverage, corrected actuals and maintenance."""
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command, CommandError
from django.utils import timezone

from config.test_support import APITestCase
from sales.models import Transaction
from .models import ProjectionCoverage, ProjectionIssue, ProjectionScore, ProvisionalForecastRun
from .prospective import attest, evidence_report, issue_current, period_end, score_closed
from .provisional import VERSION, context, digest


def clock(year, month, day):
    return patch('django.utils.timezone.now', return_value=timezone.make_aware(datetime(year, month, day, 9)))


class ProjectionEvidenceTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(username='evidence-admin', email='evidence@example.test', role='admin')
        self.staff = get_user_model().objects.create_user(username='evidence-staff', email='staff-evidence@example.test', role='staff')
        self.sale(date(2024, 12, 15), 'regular', 100)
        self.sale(date(2024, 12, 15), 'feeding', 200)

    def sale(self, day, segment, amount, reference=None):
        sale = Transaction.objects.create(handled_by=self.admin, total_amount=amount,
            source_sales_segment=segment, source_reference=reference)
        Transaction.objects.filter(pk=sale.pk).update(created_at=timezone.make_aware(datetime(day.year, day.month, day.day, 9)))
        return sale

    def make_run(self):
        snapshots, targets, revision = context()
        periods = {}
        for period, dates in targets.items():
            views = {}
            for view, amount in [('regular', 100), ('feeding', 200), ('combined', 300)]:
                value = amount*(12 if period == 'yearly' else 1)
                views[view] = {'points': [{'date': day, 'predicted': value, 'lower_bound': value/2,
                    'upper_bound': value*2, 'basis': 'test_saved_path', 'interval_basis': 'uncalibrated'} for day in dates]}
            periods[period] = views
        findings = {'targets': targets, 'periods': periods,
                    'source_revisions': {p: s.revision for p, s in snapshots.items()}}
        findings['artifact_digest'] = digest(findings)
        return ProvisionalForecastRun.objects.create(engine_version=VERSION, source_revision=revision, findings=findings)

    def review(self, period, segment, target, zero=False):
        return attest(period, segment, target, self.admin.username, 'Original period ledger reconciled', zero)

    def test_all_nine_paths_are_issued_once_without_fitting_and_refits_do_not_replace_them(self):
        with clock(2025, 1, 15), patch('forecasting.provisional.SARIMAX.fit', side_effect=AssertionError('must not fit')):
            original = self.make_run()
            self.assertEqual(issue_current(), 81)
            self.assertEqual(issue_current(), 0)
            frozen = list(ProjectionIssue.objects.values())
            self.sale(date(2024, 12, 16), 'regular', 25)
            self.make_run()
            self.assertEqual(issue_current(), 0)
            self.assertEqual(list(ProjectionIssue.objects.values()), frozen)
            self.assertEqual(set(ProjectionIssue.objects.values_list('run_id', flat=True)), {original.pk})
            yearly = ProjectionIssue.objects.get(period='yearly', view='combined', horizon=1)
            self.assertEqual(yearly.baseline_value, Decimal('3600'))
            self.assertEqual(score_closed(), 0)
        self.assertEqual(len(set(ProjectionIssue.objects.values_list('period', 'view'))), 9)

    def test_no_old_backfill_missing_run_or_corrupt_artifact(self):
        with clock(2025, 1, 15):
            self.assertEqual(issue_current(), 0)
            run = self.make_run()
            run.findings['artifact_digest'] = 'bad'
            run.save(update_fields=['findings'])
            with self.assertRaisesRegex(ValueError, 'corrupted'):
                issue_current()
        with clock(2025, 2, 15):
            self.assertEqual(issue_current(), 0)  # Old targets cannot masquerade as current.
        self.assertEqual(ProjectionIssue.objects.count(), 0)

    def test_new_origins_issue_again_but_keep_first_year_origin_predictions(self):
        with clock(2025, 1, 15):
            original = self.make_run()
            issue_current()
        with clock(2025, 2, 15):
            self.make_run()
            self.assertEqual(issue_current(), 75)  # 39 weekly + 36 monthly; same calendar-year origin.
        self.assertEqual(ProjectionIssue.objects.count(), 156)
        self.assertEqual(set(ProjectionIssue.objects.filter(period='yearly').values_list('run_id', flat=True)), {original.pk})

    def test_midnight_race_rolls_back_issues_instead_of_using_old_business_date(self):
        with clock(2025, 1, 15):
            self.make_run()
            with patch('forecasting.prospective.timezone.localdate',
                       side_effect=[date(2025, 1, 15), date(2025, 1, 15), date(2025, 1, 16)]):
                with self.assertRaisesRegex(ValueError, 'Business date changed'):
                    issue_current()
        self.assertEqual(ProjectionIssue.objects.count(), 0)

    def test_review_required_combined_requires_both_and_corrections_preserve_old_scores(self):
        with clock(2025, 1, 15):
            self.make_run()
            issue_current()
        regular = self.sale(date(2025, 2, 10), 'regular', 120)
        self.sale(date(2025, 2, 10), 'feeding', 240)
        with clock(2025, 3, 1):
            self.assertEqual(score_closed(), 0)
            self.review('monthly', 'regular', date(2025, 2, 1))
            self.assertEqual(score_closed(), 1)
            self.review('monthly', 'feeding', date(2025, 2, 1))
            self.assertEqual(score_closed(), 2)
            self.assertEqual(score_closed(), 0)
            combined = ProjectionScore.objects.get(issued__period='monthly', issued__view='combined')
            self.assertEqual(combined.actual, Decimal('360'))
            self.assertEqual(combined.error, Decimal('-60'))
            self.assertEqual(combined.baseline_absolute_error, Decimal('60'))
            self.assertTrue(combined.within_bounds)
            Transaction.objects.filter(pk=regular.pk).update(total_amount=150)
            groups = evidence_report()['groups']
            group = next(g for g in groups if g['period']=='monthly' and g['view']=='regular' and g['horizon']==1)
            self.assertEqual(group['scored_count'], 0)
            self.assertEqual(score_closed(), 0)
            self.assertEqual(ProjectionScore.objects.count(), 3)
            self.review('monthly', 'regular', date(2025, 2, 1))
            self.assertEqual(score_closed(), 2)  # regular and combined, feeding unchanged.
            self.assertEqual(ProjectionScore.objects.count(), 5)
            group = next(g for g in evidence_report()['groups'] if g['period']=='monthly' and g['view']=='regular' and g['horizon']==1)
            self.assertAlmostEqual(group['wape'], 100/3)
            self.assertEqual(group['bias'], -50)
            self.assertEqual(group['scored_count'], 1)

    def test_unknown_is_not_zero_all_horizons_can_score_after_explicit_reviews(self):
        with clock(2025, 1, 15):
            self.make_run()
            issue_current()
        with clock(2028, 1, 1):
            self.assertEqual(score_closed(), 0)
            for period, target in ProjectionIssue.objects.values_list('period', 'target').distinct():
                for segment in ('regular', 'feeding'):
                    with self.assertRaisesRegex(ValueError, 'confirm-zero'):
                        self.review(period, segment, target)
                    self.review(period, segment, target, zero=True)
            self.assertEqual(score_closed(), 81)
            self.assertEqual(score_closed(), 0)
            groups = evidence_report()['groups']
            self.assertEqual(len(groups), 81)
            self.assertTrue(all(g['scored_count']==1 and g['wape'] is None for g in groups))
            self.assertEqual(ProjectionCoverage.objects.count(), 54)

    def test_invalid_review_dates_incomplete_periods_and_unclassified_entries(self):
        with clock(2025, 2, 10):
            for period, target in [('weekly', date(2025, 2, 4)), ('monthly', date(2025, 1, 2)),
                                   ('yearly', date(2024, 2, 1)), ('monthly', date(2025, 2, 1))]:
                with self.assertRaises(ValueError):
                    self.review(period, 'regular', target)
            with self.assertRaises(ValueError):
                attest('monthly', 'regular', date(2025, 1, 1), self.admin.username, '')
            with self.assertRaises(ValueError):
                self.review('monthly', 'combined', date(2025, 1, 1))
            self.sale(date(2025, 1, 5), 'unclassified', 50)
            with self.assertRaisesRegex(ValueError, 'unclassified'):
                self.review('monthly', 'regular', date(2025, 1, 1), True)
        self.assertEqual(period_end(date(2024, 12, 30), 'weekly'), date(2025, 1, 6))
        self.assertEqual(period_end(date(2024, 1, 1), 'yearly'), date(2025, 1, 1))

    def test_nonzero_actuals_are_scored_for_every_view_and_calendar_period(self):
        with clock(2025, 1, 15):
            self.make_run()
            issue_current()
        expected = {'weekly': (date(2025, 1, 20), 10, 20),
                    'monthly': (date(2025, 2, 1), 120, 240),
                    'yearly': (date(2026, 1, 1), 700, 800)}
        for period, (target, regular, feeding) in expected.items():
            for segment, amount in [('regular', regular), ('feeding', feeding)]:
                self.sale(target, segment, amount)
        with clock(2027, 1, 1):
            for period, (target, _, _) in expected.items():
                for segment in ('regular', 'feeding'):
                    self.review(period, segment, target)
            self.assertEqual(score_closed(), 9)
            for period, (_, regular, feeding) in expected.items():
                for view, actual in [('regular', regular), ('feeding', feeding), ('combined', regular+feeding)]:
                    scored = ProjectionScore.objects.get(issued__period=period, issued__view=view)
                    self.assertEqual(scored.actual, actual)
                    self.assertEqual(scored.error, scored.issued.value-actual)

    def test_future_or_retroactively_modified_issue_timestamp_cannot_be_scored(self):
        with clock(2025, 1, 15):
            self.make_run()
            issue_current()
        with clock(2025, 3, 1):
            self.review('monthly', 'regular', date(2025, 2, 1), True)
            ProjectionIssue.objects.filter(period='monthly', view='regular').update(issued_at=timezone.now())
            self.assertEqual(score_closed(), 0)

    def test_commands_and_read_only_admin_api(self):
        self.client.force_authenticate(self.admin)
        with clock(2025, 1, 15):
            self.make_run()
            output = StringIO()
            call_command('refresh_projection_evidence', stdout=output)
            self.assertEqual(json.loads(output.getvalue())['issues_created'], 81)
            with patch('forecasting.prospective.issue_current', side_effect=AssertionError('GET must not write')):
                response = self.client.get('/api/forecasting/evidence/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.data['groups']), 81)
            self.assertFalse(response.data['production_approved'])
            self.assertEqual(self.client.post('/api/forecasting/evidence/', {}).status_code, 405)
        for user, status in [(self.staff, 403), (None, 401)]:
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get('/api/forecasting/evidence/').status_code, status)
        with clock(2025, 3, 1):
            with self.assertRaises(CommandError):
                call_command('verify_projection_coverage', 'monthly', 'regular', '2025-02-01',
                             admin_username=self.admin.username, note='Reconciled')
            output = StringIO()
            call_command('verify_projection_coverage', 'monthly', 'regular', '2025-02-01',
                         admin_username=self.admin.username, note='Reconciled true zero', confirm_zero=True, stdout=output)
            self.assertTrue(json.loads(output.getvalue())['confirmed_zero'])
