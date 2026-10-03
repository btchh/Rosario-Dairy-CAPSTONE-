"""Serve frozen SARIMA evaluations without fitting during requests."""
from datetime import timedelta

from django.utils import timezone

from .contract import VERSION, TARGET_PERCENT, LIMITATIONS, options, period_acceptance
from .data import source
from .models import ForecastRun, IssuedForecast

PERIODS = ('weekly', 'monthly', 'yearly')


def report(scope='real', period='monthly'):
    if period not in PERIODS:
        raise ValueError('Invalid forecast period')
    daily, signature, warnings = source(scope)
    payload = {
        'generated_at': timezone.now(), 'horizon_days': {'weekly':7, 'monthly':30, 'yearly':365}[period],
        'method':'unavailable', 'is_placeholder':False, 'forecast':[], 'status':'pending_update',
        'period':period, 'available_periods':list(PERIODS), 'scope':scope,
        'accuracy_target_percent':TARGET_PERCENT, 'accuracy_metric':'2025_retrospective_wape',
        'regular':{}, 'bulk':{'status':'not_used'}, 'combined':{}, 'cutoff':{},
        'metrics':{}, 'baselines':{}, 'historical_comparison':[],
        'limitations':LIMITATIONS, 'warnings':warnings,
    }
    fingerprint = options(scope).signature()
    run = ForecastRun.objects.filter(scope=scope, version=VERSION,
        configuration_signature=fingerprint, source_signature=signature).first()
    if not run:
        return payload
    result = run.result
    setup = result.get('setups', {}).get(result.get('selected_setup'))
    if not setup:
        return payload
    evaluation = setup.get('periods', {}).get(period)
    if not evaluation:
        payload['status'] = 'insufficient_history'
        return payload
    metrics = evaluation['metrics']
    quality = period_acceptance(metrics['combined'])
    point = evaluation['next_period']
    payload.update(
        generated_at=run.created_at, data_end=run.data_end, method=evaluation['method'], status=quality,
        quality_status=quality, metrics=metrics, historical_comparison=evaluation['rows'],
        fixed_origin_comparison=evaluation.get('fixed_origin_rows', []),
        fixed_origin_metrics=evaluation.get('fixed_origin_metrics'),
        regular={'status':quality, 'metrics':metrics['combined'], 'aggregation':period},
        baselines=evaluation.get('baselines', {}),
        component_metrics=evaluation.get('component_metrics', {}),
        component_comparison=evaluation.get('component_comparison', {}),
        evaluation_details={'strategy':evaluation.get('strategy'),
                            'validation_end':evaluation.get('validation_end'),
                            'selection_signature':evaluation.get('selection_signature'),
                            'models':{name: selection['model'] for name, selection in
                                      evaluation.get('selection', {}).get('models', {}).items()}},
        cutoff={'selected_setup':result['selected_setup'],
                'selection_trained_through':result['selection_trained_through'],
                'configuration':result['configuration']},
        data_provenance=result.get('data_provenance', {}),
    )
    if run.data_end != timezone.localdate() - timedelta(days=1):
        payload['warnings'] = [*warnings, 'Sales have not been recorded through yesterday; current projections are withheld.']
        if quality == 'accepted':
            payload['status'] = 'stale_data'
        return payload
    if quality != 'accepted':
        return payload
    if point['status'] != 'ready':
        payload['status'] = point['status']
        return payload
    issued = IssuedForecast.objects.filter(scope=scope, version=VERSION,
        configuration_signature=fingerprint, setup=setup['setup'],
        period=period, date=point['date']).first()
    if not issued:
        payload['status'] = 'pending_update'
        return payload
    point = issued.payload
    payload['combined'] = point['combined']
    payload['regular'].update(point.get('regular', point['combined']))
    payload['horizon_days'] = point['days']
    payload['forecast'] = [{'date':point['date'], 'end_date':point['end_date'],
                            'trained_through':point['trained_through'], **point['combined']}]
    payload['status'] = 'ready'
    return payload
