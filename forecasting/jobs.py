"""Fit offline under an advisory lock; atomically publish derived snapshots only."""
from datetime import date,timedelta
from zoneinfo import ZoneInfo
from django.db import connection,transaction
from django.utils import timezone
from systemsetting.runtime import get_runtime_settings
from .contract import VERSION,options as configured_options,period_acceptance
from .data import source,as_series
from .models import ForecastRun,IssuedForecast


def refresh(scope='real',options=None,progress=None):
    from .evaluation import evaluate
    options=options or configured_options(scope);fingerprint=options.signature()
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)',[82197033])
        if not cursor.fetchone()[0]:raise ValueError('A forecast evaluation is already running')
    try:
        with timezone.override(ZoneInfo(get_runtime_settings()['timezone'])):
            daily,signature,warnings=source(scope)
            previous=ForecastRun.objects.filter(scope=scope,version=VERSION,
                configuration_signature=fingerprint,source_signature=signature).first()
            if previous:return previous,False
            result=evaluate(as_series(daily),options,progress)
            result['warnings']=warnings
            if source(scope)[1]!=signature:raise ValueError('Source sales changed during evaluation; retry')
            with transaction.atomic():
                selected=result['setups'].get(result['selected_setup'])
                current=date.fromisoformat(result['data_end'])==timezone.localdate()-timedelta(days=1)
                if selected and current:
                    for period,evaluation in selected['periods'].items():
                        point=evaluation['next_period']
                        if period_acceptance(evaluation['metrics']['combined'])!='accepted' or not point or point['status']!='ready':continue
                        issued,_=IssuedForecast.objects.get_or_create(scope=scope,version=VERSION,
                            configuration_signature=fingerprint,setup=selected['setup'],period=period,date=point['date'],defaults={
                                'trained_through':point['trained_through'],'payload':point})
                        selected['periods'][period]['next_period']=issued.payload
                run=ForecastRun.objects.create(scope=scope,version=VERSION,configuration_signature=fingerprint,
                    source_signature=signature,data_end=result['data_end'],result=result)
            return run,True
    finally:
        with connection.cursor() as cursor:cursor.execute('SELECT pg_advisory_unlock(%s)',[82197033])
