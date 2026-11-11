"""Configurable analyses reuse the global maintenance lock and refresh jobs."""
import logging
from django.db import transaction
from django.utils import timezone
from .analysis_workspace import VERSION, build_analysis
from .hierarchy_source import load_source
from .methods import ForecastConfigurationError
from .models import ForecastAnalysisRun, ForecastRefreshJob
from .provisional import digest
from .refresh_jobs import maintenance_lock, _recover_abandoned, launch_after_commit

logger=logging.getLogger(__name__)


def enqueue_analysis(configuration,user):
    with maintenance_lock() as acquired:
        if not acquired: return None
        _recover_abandoned()
        if ForecastRefreshJob.objects.filter(status__in=['queued','running']).exists(): return None
        with transaction.atomic():
            job=ForecastRefreshJob.objects.create(scope='analysis',trigger='manual',requested_by=user)
            run=ForecastAnalysisRun.objects.create(job=job,engine_version=VERSION,configuration=configuration)
            launch_after_commit(job)
            return run


def execute_analysis(job):
    """Called only while run_maintenance owns the shared advisory lock."""
    run=job.analysis_run
    job.status='running'; job.stage='prepare_forecast_analysis'; job.started_at=timezone.now()
    job.save(update_fields=['status','stage','started_at'])
    try:
        source=load_source()
        findings=build_analysis(run.configuration,source)
        if load_source()['revision']!=source['revision']: raise ValueError('Recorded sales or catalog changed during analysis; run it again.')
        run.findings=findings; run.source_revision=source['revision']; run.checksum=digest(findings)
        run.save(update_fields=['findings','source_revision','checksum'])
        job.status='succeeded'; job.stage='complete'; job.results={'analysis_run_id':run.pk}
    except Exception as error:
        logger.exception('Configurable forecast analysis %s failed',run.pk)
        job.status='failed'; job.error_code='analysis_failed'
        job.results={'analysis_run_id':run.pk,'detail':str(error) if isinstance(error,ForecastConfigurationError) else 'The analysis could not be completed. The ledger may have changed; review the configuration and retry. Server logs retain the details.'}
    finally:
        job.finished_at=timezone.now(); job.save(update_fields=['status','stage','error_code','results','finished_at'])
    return {'status':job.status,'job_id':job.pk,'results':job.results}
