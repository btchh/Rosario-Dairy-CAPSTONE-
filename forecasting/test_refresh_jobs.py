from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction, connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from config.test_support import APITestCase
from .models import ForecastRefreshJob
from .refresh_jobs import (STAGES, enqueue, lookup, maintenance_lock,
                           run_maintenance)


def fake_command(name, **kwargs):
    kwargs['stdout'].write('{"created": false}\n')


@contextmanager
def busy_lock():
    yield False


class ForecastRefreshAPITests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(username='refresh-admin', email='refresh-admin@example.test', role='admin')
        self.staff = get_user_model().objects.create_user(username='refresh-staff', email='refresh-staff@example.test', role='staff')
        self.client.force_authenticate(self.admin)

    def test_manual_request_is_async_tracked_and_duplicate_reuses_job(self):
        with patch('forecasting.refresh_jobs.launch') as launch, \
                patch('forecasting.refresh_jobs.call_command', side_effect=AssertionError('HTTP must not fit')):
            with self.capture_commit_callbacks(execute=True):
                response = self.client.post('/api/forecasting/refresh/', {}, format='json')
            self.assertEqual(response.status_code, 202)
            job_id = response.data['job_id']
            launch.assert_called_once_with(job_id)
            cache.clear()
            again = self.client.post('/api/forecasting/refresh/', {}, format='json')
            self.assertEqual(again.data['job_id'], job_id)
            self.assertTrue(again.data['already_running'])
            self.assertEqual(ForecastRefreshJob.objects.count(), 1)
            status = self.client.get(response['Location'])
            self.assertEqual(status.data['status'], 'queued')
            self.assertEqual(self.client.get('/api/forecasting/refresh/').data['job_id'], job_id)

    def test_permissions_and_empty_body_and_missing_job(self):
        for user, expected in [(self.staff, 403), (None, 401)]:
            self.client.force_authenticate(user)
            self.assertEqual(self.client.post('/api/forecasting/refresh/', {}).status_code, expected)
            self.assertEqual(self.client.get('/api/forecasting/refresh/').status_code, expected)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post('/api/forecasting/refresh/', {'scope': 'demo'}, format='json').status_code, 400)
        self.assertEqual(ForecastRefreshJob.objects.count(), 0)
        self.assertEqual(self.client.get('/api/forecasting/refresh/12345/').status_code, 404)
        self.assertEqual(self.client.get('/api/forecasting/refresh/').data['status'], 'idle')

    def test_start_rate_limit_does_not_limit_status_polling_as_a_write(self):
        with patch('forecasting.refresh_jobs.launch'):
            self.assertEqual(self.client.post('/api/forecasting/refresh/', {}).status_code, 202)
            self.assertEqual(self.client.post('/api/forecasting/refresh/', {}).status_code, 429)
            self.assertEqual(self.client.get('/api/forecasting/refresh/').status_code, 200)

    def test_launch_failure_is_pollable_and_safe_to_retry(self):
        with patch('forecasting.refresh_jobs.launch', side_effect=OSError('sensitive backend path')):
            with self.capture_commit_callbacks(execute=True):
                response = self.client.post('/api/forecasting/refresh/', {})
        result = self.client.get(response['Location'])
        self.assertEqual(result.data['status'], 'failed')
        self.assertEqual(result.data['error_code'], 'worker_launch_failed')
        self.assertNotIn('sensitive', str(result.data))
        cache.clear()
        retry = self.client.post('/api/forecasting/refresh/', {})
        self.assertNotEqual(retry.data['job_id'], response.data['job_id'])


class ForecastRefreshWorkerTests(TestCase):
    def test_actual_completed_period_correction_retrains_and_unchanged_data_reuses(self):
        from sales.models import Transaction
        from .models import ProvisionalForecastRun, MonthlyIssuedForecast
        from .provisional import train
        user = get_user_model().objects.create_user(username='correction-admin', email='correction@example.test', role='admin')
        correction = None
        for month in range(1, 7):
            for segment in ('regular', 'feeding'):
                sale = Transaction.objects.create(handled_by=user, total_amount=100+month,
                    source_sales_segment=segment, source_reference=f'2025:REFRESH:{segment}:{month}')
                Transaction.objects.filter(pk=sale.pk).update(created_at=timezone.make_aware(datetime(2025, month, 15)))
                if month == 6 and segment == 'regular':
                    correction = sale
        with patch('django.utils.timezone.now', return_value=timezone.make_aware(datetime(2026, 10, 22))):
            first = run_maintenance()
            self.assertEqual(first['status'], 'succeeded')
            first_id = first['results']['refresh_provisional_forecasts']['model_id']
            frozen = ProvisionalForecastRun.objects.get(pk=first_id).findings
            with patch('forecasting.provisional.train', side_effect=AssertionError('unchanged input must reuse')):
                repeated = run_maintenance()
            self.assertEqual(repeated['status'], 'succeeded')
            self.assertTrue(all(not item['created'] for item in repeated['results'].values()))
            assert correction is not None
            Transaction.objects.filter(pk=correction.pk).update(total_amount=999)
            with patch('forecasting.provisional.train', wraps=train) as training:
                changed = run_maintenance()
            self.assertEqual(changed['status'], 'succeeded')
            self.assertEqual(training.call_count, 2)  # Only the two regular components changed.
        for stage in STAGES:
            self.assertTrue(changed['results'][stage]['created'])
        self.assertNotEqual(changed['results']['refresh_provisional_forecasts']['model_id'], first_id)
        self.assertEqual(ProvisionalForecastRun.objects.get(pk=first_id).findings, frozen)
        self.assertEqual(MonthlyIssuedForecast.objects.count(), 0)

    def test_worker_runs_all_stages_updates_progress_and_reuses_terminal_job(self):
        job = ForecastRefreshJob.objects.create(trigger='manual')
        stages = []
        def command(name, **kwargs):
            current = ForecastRefreshJob.objects.get(pk=job.pk)
            self.assertEqual(current.status, 'running')
            self.assertEqual(current.stage, name)
            stages.append(name)
            fake_command(name, **kwargs)
        with patch('forecasting.refresh_jobs.call_command', side_effect=command):
            result = run_maintenance(job.pk)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(stages, list(STAGES))
        job.refresh_from_db()
        self.assertEqual(job.stage, 'complete')
        self.assertIsNotNone(job.finished_at)
        self.assertIsNotNone(job.started_at)
        self.assertEqual(set(job.results), set(STAGES))
        with patch('forecasting.refresh_jobs.call_command', side_effect=AssertionError('do not rerun finished job')):
            self.assertEqual(run_maintenance(job.pk)['status'], 'succeeded')

    def test_failed_stage_preserves_completed_results_and_releases_lock(self):
        def command(name, **kwargs):
            if name == 'refresh_weekly_forecasts':
                raise ValueError('private source details')
            fake_command(name, **kwargs)
        job = ForecastRefreshJob.objects.create(trigger='manual')
        with patch('forecasting.refresh_jobs.call_command', side_effect=command):
            result = run_maintenance(job.pk)
        self.assertEqual(result['status'], 'failed')
        job.refresh_from_db()
        self.assertEqual(job.error_code, 'source_changed')
        self.assertEqual(set(job.results), set(STAGES[:2]))
        with maintenance_lock() as acquired:
            self.assertTrue(acquired)
        replacement, created = enqueue(None)
        self.assertTrue(created)
        self.assertNotEqual(replacement.pk, job.pk)

    def test_busy_worker_never_fits_or_creates_duplicate_scheduled_job(self):
        job = ForecastRefreshJob.objects.create(trigger='manual', status='running')
        with patch('forecasting.refresh_jobs.maintenance_lock', busy_lock), \
                patch('forecasting.refresh_jobs.call_command', side_effect=AssertionError('busy must not fit')):
            self.assertEqual(run_maintenance()['status'], 'busy')
            self.assertEqual(enqueue(None), (job, False))
            self.assertEqual(lookup().status, 'running')
        self.assertEqual(ForecastRefreshJob.objects.count(), 1)

    def test_abandoned_running_or_old_queued_jobs_are_recoverable(self):
        abandoned = ForecastRefreshJob.objects.create(trigger='manual', status='running')
        self.assertEqual(lookup(abandoned.pk).error_code, 'worker_interrupted')
        pending = ForecastRefreshJob.objects.create(trigger='manual')
        ForecastRefreshJob.objects.filter(pk=pending.pk).update(created_at=timezone.now()-timedelta(minutes=11))
        self.assertEqual(lookup(pending.pk).error_code, 'worker_not_started')
        job, created = enqueue(None)
        self.assertTrue(created)
        self.assertEqual(job.status, 'queued')

    def test_schedule_adopts_pending_manual_job_instead_of_duplicating_it(self):
        job = ForecastRefreshJob.objects.create(trigger='manual')
        with patch('forecasting.refresh_jobs.call_command', side_effect=fake_command):
            result = run_maintenance()
        self.assertEqual(result['job_id'], job.pk)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(ForecastRefreshJob.objects.count(), 1)

    def test_database_guarantees_one_active_job(self):
        ForecastRefreshJob.objects.create(trigger='manual')
        with self.assertRaises(IntegrityError), transaction.atomic():
            ForecastRefreshJob.objects.create(trigger='scheduled', status='running')

    def test_launcher_uses_backend_interpreter_and_no_shell(self):
        from .refresh_jobs import launch
        from django.conf import settings
        import sys
        with patch('forecasting.refresh_jobs.subprocess.Popen') as process, \
                patch('forecasting.refresh_jobs.Thread') as thread:
            launch(999999)
        args, kwargs = process.call_args
        self.assertEqual(args[0], [sys.executable, str(settings.BASE_DIR/'manage.py'),
                                   'run_forecast_refresh', '--job-id', '999999'])
        self.assertTrue(kwargs['start_new_session'])
        self.assertNotIn('shell', kwargs)
        thread.return_value.start.assert_called_once()
        (settings.BASE_DIR/'backups'/'forecast-refresh'/'job-999999.log').unlink(missing_ok=True)


class ForecastRefreshConcurrencyTests(TransactionTestCase):
    def test_other_db_session_cannot_overlap_fitting(self):
        from concurrent.futures import ThreadPoolExecutor
        def another_session():
            try:
                with maintenance_lock() as acquired:
                    return acquired
            finally:
                connections['default'].close()
        with maintenance_lock() as acquired:
            self.assertTrue(acquired)
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertFalse(pool.submit(another_session).result(timeout=5))
        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertTrue(pool.submit(another_session).result(timeout=5))
