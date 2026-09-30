"""Read preserved source tables and completed live sales without changing them."""
from datetime import datetime,time,timedelta
from decimal import Decimal
from hashlib import sha256
import json

from django.db import connection
from django.db.models import Q,Sum,Count
from django.db.models.functions import TruncDate
from django.utils import timezone
from sales.models import Transaction
from calendar import isleap


def combine_scenario(real,simulated):
    """A derived view: never add overlapping revenues or mutate either input."""
    daily={**simulated,**real}
    real_dates=set(real);dummy_dates=set(simulated)-real_dates
    overlap=real_dates & set(simulated)
    gap_days=sum(min(real)<=d<=max(real) for d in dummy_dates) if real else 0
    years=[]
    for year in sorted({d.year for d in daily}):
        dates=[d for d in daily if d.year==year]
        if len(dates)!=365+isleap(year):continue
        years.append({'year':year,'revenue':str(sum((daily[d] for d in dates),Decimal('0.00'))),
            'real_days':sum(d in real_dates for d in dates),
            'simulated_days':sum(d in dummy_dates for d in dates)})
    provenance={'kind':'mixed_scenario_not_real_sales','merge_rule':'real_overrides_simulated_same_date',
        'independent_accuracy_evidence':False,'observed_days':len(daily),
        'real_days':len(real),'simulated_days':len(dummy_dates),
        'overlap_days_replaced':len(overlap),'simulated_gap_days':gap_days,
        'simulated_outside_real_span_days':len(dummy_dates)-gap_days,
        'data_start':str(min(daily)) if daily else None,'data_end':str(max(daily)) if daily else None,
        'complete_calendar_years':years}
    return daily,provenance


def mixed_source(scope):
    identifier=int(scope.split(':',1)[1])
    if identifier<=0:raise ValueError('Invalid mixed scenario id')
    real,real_signature,real_warnings=source('real')
    simulated,dummy_signature,_=source(f'simulation:{identifier}')
    daily,provenance=combine_scenario(real,simulated)
    provenance.update(real_source_signature=real_signature,simulated_source_signature=dummy_signature)
    signature=sha256(json.dumps({'scope':scope,'sources':[real_signature,dummy_signature],
        'merge_rule':provenance['merge_rule']},sort_keys=True).encode()).hexdigest()
    warnings=[*real_warnings,
        'MIXED SCENARIO: real sales take priority; other dates are simulated, including gaps. Not real-business accuracy evidence.',
        'Dummy data was calibrated from real history: earlier synthetic dates may encode later real patterns. This is not an independent leakage-free accuracy test.',
        f"Uses {provenance['real_days']} real days and {provenance['simulated_days']} simulated days; {provenance['overlap_days_replaced']} overlapping dummy days are replaced, not added."]
    return daily,signature,warnings,provenance


def source(scope='real'):
    if scope.startswith('mixed:'):
        daily,signature,warnings,_=mixed_source(scope)
        return daily,signature,warnings
    with connection.cursor() as cursor:
        tables=set(connection.introspection.table_names(cursor))
        if scope.startswith('simulation:'):
            identifier=int(scope.split(':',1)[1])
            if identifier<=0:raise ValueError('Invalid simulation id')
            if 'reporting_simulationdataset' not in tables:raise ValueError('Simulation is unavailable')
            cursor.execute('SELECT id FROM reporting_simulationdataset WHERE id=%s',[identifier])
            if cursor.fetchone() is None:raise ValueError('Simulation does not exist')
            cursor.execute('SELECT date,revenue FROM reporting_simulatedsalesday WHERE dataset_id=%s ORDER BY date',[identifier])
            daily=dict(cursor.fetchall())
            warnings=['SIMULATED DATA: accuracy is not evidence of real-business performance.']
        elif scope=='real':
            today=timezone.localdate()
            daily={}
            if 'reporting_historicalsalesday' in tables:
                cursor.execute('SELECT date,revenue FROM reporting_historicalsalesday WHERE date<%s ORDER BY date',[today])
                daily=dict(cursor.fetchall())
            cutoff=timezone.make_aware(datetime.combine(today,time.min))
            live=Transaction.objects.filter(created_at__lt=cutoff).annotate(
                day=TruncDate('created_at',tzinfo=timezone.get_current_timezone())).values('day').annotate(
                revenue=Sum('total_amount',filter=Q(is_voided=False),default=Decimal('0.00')),
                count=Count('pk')).order_by('day')
            for row in live:
                if row['day'] in daily:raise ValueError('Imported and live revenue overlap; resolve before training')
                daily[row['day']]=row['revenue']
            warnings=['Imported raw/dairy coverage must match live product/accounting coverage.'] if 'reporting_historicalsalesday' in tables else []
        else:raise ValueError('Invalid forecast scope')
    signature=sha256(json.dumps({'scope':scope,'timezone':timezone.get_current_timezone_name(),
        'daily':[(str(d),str(v)) for d,v in sorted(daily.items())]},sort_keys=True,default=str).encode()).hexdigest()
    return daily,signature,warnings


def as_series(daily):
    import pandas as pd
    return pd.Series([float(v) for _,v in sorted(daily.items())],
        index=pd.DatetimeIndex([d for d,_ in sorted(daily.items())]),dtype=float)
