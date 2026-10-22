"""One process owns forecast maintenance; API requests only enqueue and poll.

PostgreSQL session advisory locking covers the entire fit without holding a
transaction/table lock over computation. The same lock is used by midnight runs.
"""
from contextlib import contextmanager
from datetime import timedelta
from io import StringIO
import json
import logging
import subprocess
import sys
from threading import Thread

from django.conf import settings
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone

from .models import ForecastRefreshJob

logger = logging.getLogger(__name__)
LOCK_ID = 185213773
ACTIVE = ('queued', 'running')
STAGES = ('refresh_forecast_patterns', 'refresh_forecast_models',
          'refresh_weekly_forecasts', 'refresh_provisional_forecasts')


@contextmanager
def maintenance_lock():
    """Nonblocking session lock, released even on an exception/process exit."""
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [LOCK_ID])
        acquired = cursor.fetchone()[0]
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [LOCK_ID])


def _recover_abandoned():
    """Only call while owning the lock: an actual fitting worker cannot own it."""
    now = timezone.now()
    ForecastRefreshJob.objects.filter(status='running').update(
        status='failed', stage='interrupted', error_code='worker_interrupted', finished_at=now)
    ForecastRefreshJob.objects.filter(status='queued', created_at__lt=now-timedelta(minutes=10)).update(
        status='failed', stage='interrupted', error_code='worker_not_started', finished_at=now)


def enqueue(user):
    with maintenance_lock() as acquired:
        if acquired:
            _recover_abandoned()
            with transaction.atomic():
                active = ForecastRefreshJob.objects.filter(status__in=ACTIVE).first()
                if active:
                    return active, False
                return ForecastRefreshJob.objects.create(trigger='manual', requested_by=user), True
        active = ForecastRefreshJob.objects.filter(status__in=ACTIVE).first()
        if active:
            return active, False
        # A scheduled worker owns the lock but has not saved its running row yet.
        return None, False


def launch(job_id):
    """The child owns its DB connection and continues after the HTTP request."""
    folder = settings.BASE_DIR/'backups'/'forecast-refresh'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder/f'job-{job_id}.log'
    with path.open('a') as output:
        path.chmod(0o600)
        process = subprocess.Popen(
            [sys.executable, str(settings.BASE_DIR/'manage.py'), 'run_forecast_refresh', '--job-id', str(job_id)],
            cwd=str(settings.BASE_DIR), stdin=subprocess.DEVNULL, stdout=output, stderr=output,
            close_fds=True, start_new_session=True)
    Thread(target=process.wait, daemon=True).start()


def launch_after_commit(job):
    def start():
        try:
            launch(job.pk)
        except Exception:
            logger.exception('Unable to launch forecast refresh job %s', job.pk)
            ForecastRefreshJob.objects.filter(pk=job.pk, status='queued').update(
                status='failed', stage='launch_failed', error_code='worker_launch_failed', finished_at=timezone.now())
    transaction.on_commit(start)


def run_maintenance(job_id=None):
    with maintenance_lock() as acquired:
        if not acquired:
            return {'status': 'busy', 'job_id': ForecastRefreshJob.objects.filter(status__in=ACTIVE).values_list('pk', flat=True).first()}
        _recover_abandoned()
        if job_id is None:
            job = ForecastRefreshJob.objects.filter(status='queued').first()
            if job is None:
                job = ForecastRefreshJob.objects.create(trigger='scheduled')
        else:
            job = ForecastRefreshJob.objects.get(pk=job_id)
        if job.status != 'queued':
            return {'status': job.status, 'job_id': job.pk}
        job.status = 'running'
        job.started_at = timezone.now()
        job.save(update_fields=['status', 'started_at'])
        try:
            for stage in STAGES:
                job.stage = stage
                job.save(update_fields=['stage'])
                output = StringIO()
                call_command(stage, stdout=output)
                job.results[stage] = json.loads(output.getvalue().strip().splitlines()[-1])
                job.save(update_fields=['results'])
            job.status = 'succeeded'
            job.stage = 'complete'
        except Exception as exc:
            logger.exception('Forecast refresh job %s failed during %s', job.pk, job.stage)
            job.status = 'failed'
            job.error_code = 'source_changed' if isinstance(exc, ValueError) else 'refresh_failed'
        finally:
            job.finished_at = timezone.now()
            job.save(update_fields=['status', 'stage', 'error_code', 'finished_at', 'results'])
        return {'status': job.status, 'job_id': job.pk, 'results': job.results}


def lookup(job_id=None):
    with maintenance_lock() as acquired:
        if acquired:
            _recover_abandoned()
    query = ForecastRefreshJob.objects.all()
    return query.filter(pk=job_id).first() if job_id is not None else query.first()


def payload(job):
    if job is None:
        return {'status': 'idle', 'job_id': None}
    messages = {'queued': 'Forecast refresh is queued.', 'running': 'Forecast models are being refreshed.',
                'succeeded': 'Forecast refresh completed. Reload the forecast graph.',
                'failed': 'Forecast refresh failed. You can retry; completed model versions are retained.'}
    return {'job_id': job.pk, 'status': job.status, 'stage': job.stage, 'trigger': job.trigger,
            'created_at': job.created_at, 'started_at': job.started_at, 'finished_at': job.finished_at,
            'results': job.results, 'error_code': job.error_code or None, 'message': messages[job.status],
            'status_url': f'/api/forecasting/refresh/{job.pk}/'}
