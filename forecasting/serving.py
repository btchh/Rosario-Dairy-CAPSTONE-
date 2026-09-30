"""Read evaluated period snapshots; daily paths stay internal to SARIMA."""
from datetime import timedelta
from django.utils import timezone
from .contract import VERSION,TARGET_PERCENT,LIMITATIONS,options,period_acceptance
from .data import source
from .models import ForecastRun,IssuedForecast

PERIODS=('weekly','monthly','yearly')


def report(scope='real',period='monthly'):
    if period not in PERIODS:raise ValueError('Invalid forecast period')
    daily,signature,warnings=source(scope)
    payload={'generated_at':timezone.now(),'horizon_days':{'weekly':7,'monthly':30,'yearly':365}[period],
        'method':'unavailable','is_placeholder':False,'forecast':[],'status':'pending_update',
        'period':period,'available_periods':list(PERIODS),'scope':scope,'accuracy_target_percent':TARGET_PERCENT,
        'accuracy_metric':'combined_period_wape','regular':{},'bulk':{},'combined':{},
        'cutoff':{},'metrics':{},'baselines':{},'historical_comparison':[],
        'limitations':LIMITATIONS,'warnings':warnings}
    fingerprint=options(scope).signature()
    run=ForecastRun.objects.filter(scope=scope,version=VERSION,configuration_signature=fingerprint).first()
    if not run or run.source_signature!=signature:return payload
    result=run.result;setup=result['setups'].get(result['selected_setup'])
    if 'data_provenance' in result:payload['data_provenance']=result['data_provenance']
    payload.update(generated_at=run.created_at,status=result['selection_status'],
        cutoff={'selected_setup':result['selected_setup'],'selection':result['selection'],
            'selection_trained_through':result['selection_trained_through'],'configuration':result['configuration']})
    if setup is None:return payload
    evaluation=setup['periods'].get(period)
    if not evaluation:payload['status']='insufficient_history';return payload
    point=evaluation['next_period'];metrics=evaluation['metrics'];quality=period_acceptance(metrics['combined'])
    payload.update(method=point['method'],status=quality,quality_status=quality,
        regular={'status':quality,'metrics':metrics['regular'],'model':point.get('model',{}),'aggregation':period},
        metrics=metrics,historical_comparison=evaluation['rows'],
        bulk={**point['bulk'],'date':point['date'],'end_date':point['end_date'],'as_of':str(run.data_end)},
        baselines={name:metrics[f'{name}_baselines'] for name in ('regular','combined','bulk')})
    payload['cutoff']['fallback_days']=setup['fallback_days']
    payload['horizon_days']=point['days']
    if metrics['combined']['rows']==0 and point['status']=='insufficient_history':
        payload['status']='insufficient_history'
        payload['warnings'].append(f'Insufficient complete {period} history for evaluation and bulk risk estimation.')
        return payload
    if scope=='real' and run.data_end!=timezone.localdate()-timedelta(days=1):
        payload['status']='stale_data';payload['bulk']['status']='historical_only'
        payload['warnings'].append('Current completed sales are unavailable; projections are withheld.')
        return payload
    if quality!='accepted':return payload
    if point['status']!='ready':payload['status']=point['status'];return payload
    issued=IssuedForecast.objects.filter(scope=scope,version=VERSION,configuration_signature=fingerprint,
        setup=setup['setup'],period=period,date=point['date']).first()
    if not issued:payload['status']='pending_update';return payload
    point=issued.payload
    payload['regular'].update(point['regular']);payload['combined']=point['combined']
    payload['bulk']={**point['bulk'],'date':point['date'],'end_date':point['end_date'],
        'as_of':point['trained_through']}
    payload['horizon_days']=point['days']
    payload['forecast']=[{'date':point['date'],'end_date':point['end_date'],
        'trained_through':point['trained_through'],**point['combined']}]
    payload['status']='ready'
    return payload
