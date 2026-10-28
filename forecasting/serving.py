"""Read saved fits/issues only. HTTP requests never train a model."""
from django.utils import timezone
from .data import load_snapshot
from .models import PatternStudy, MonthlyModelRun, MonthlyDeployment, MonthlyIssuedForecast
from .patterns import ANALYZER_VERSION, TARGET_WAPE_PERCENT
from .learning import ENGINE_VERSION, month_end, month_start
from .lifecycle import _snapshot, valid_coverage, coverage_revision, quality_gate


def model_context():
    snapshot = _snapshot()
    coverage = valid_coverage(snapshot)
    run = MonthlyModelRun.objects.filter(engine_version=ENGINE_VERSION, source_revision=snapshot.revision,
        coverage_revision=coverage_revision(coverage), cutoff=snapshot.as_of).first()
    latest = run or MonthlyModelRun.objects.filter(engine_version=ENGINE_VERSION).first()
    gate = quality_gate(run, snapshot, coverage) if run else None
    deployment = MonthlyDeployment.objects.filter(scope='regular-monthly').first()
    issued = (MonthlyIssuedForecast.objects.filter(model_run=run, target_month=month_start(timezone.localdate())).first()
              if run else None)
    if run is None:
        status = 'refresh_required' if latest else 'training_required'
    elif run.status != 'research_candidate':
        status = run.status
    elif 'stale_training_data' in gate['reasons']:
        status = 'stale_data'
    elif gate['eligible'] and deployment and deployment.model_run_id == run.pk and issued:
        status = 'ready'
    else:
        status = 'research_only'
    return snapshot, run, latest, gate, issued, status


def models_report():
    snapshot, run, latest, gate, issued, status = model_context()
    findings = latest.findings if latest else {}
    artifact = findings.get('artifact')
    artifact = artifact if isinstance(artifact, dict) else {}
    return {
        'status': status, 'stage': 'monthly_model_learning', 'source_revision': snapshot.revision,
        'cutoff': snapshot.as_of, 'model_id': latest.pk if latest else None,
        'engine_version': ENGINE_VERSION, 'model_is_current': run is not None,
        'selected_candidate': findings.get('selected_candidate'),
        'model_family': findings.get('model_family'),
        'sarima': {k: artifact.get(k) for k in
                   ('order', 'seasonal_order', 'trend', 'converged', 'statsmodels_version',
                    'optimization_attempts', 'fit_warnings')},
        'trained_through': latest.trained_through if latest else None,
        'artifact_sha256': findings.get('artifact_sha256'),
        'research_forecast': findings.get('next_month'),
        'research_forecast_is_live': False,
        'evaluation': {k: findings.get(k) for k in ('selection_basis', 'validation_start', 'validation_end',
                    'test_start', 'test_end', 'selected_test', 'selected_baseline', 'baseline_test', 'retrospective_gate')},
        'candidates': {name: {k: v for k, v in candidate.items() if k not in ('validation_rows', 'test_rows')}
                       for name, candidate in findings.get('candidates', {}).items()},
        'quality_gate': gate, 'issued_forecast_id': issued.pk if issued else None,
        'limitations': findings.get('limitations', []),
        'versions': list(MonthlyModelRun.objects.values(
            'id', 'engine_version', 'status', 'cutoff', 'trained_through', 'source_revision', 'created_at')),
    }


def issued_point(run, issued):
    return {'date': issued.target_month, 'end_date': month_end(issued.target_month),
        'predicted_revenue': issued.value, 'lower_bound': issued.lower_bound,
        'upper_bound': issued.upper_bound, 'trained_through': run.trained_through,
        'range_kind': 'historical_absolute_error_band', 'point_kind': 'regular_monthly_revenue',
        'sample_count': run.findings['next_month']['sample_count']}


def report(scope='real',period='monthly'):
    if scope!='real' or period not in ('weekly','monthly','yearly'):
        raise ValueError('Invalid forecast scope or period')
    snapshot, run, latest, gate, issued, model_status = model_context()
    through=max((s.day for s in snapshot.sales),default=None)
    payload = {
        'generated_at':timezone.now(),'horizon_days':{'weekly':7,'monthly':30,'yearly':365}[period],
        'method':'unavailable','is_placeholder':False,'forecast':[],'status':'pending_update',
        'period':period,'scope':scope,'available_periods':['weekly','monthly','yearly'],
        'status_message':'The previous engine has been retired. The replacement is investigating regular-sales patterns; no new prediction model has been validated.',
        'accuracy_target_percent':TARGET_WAPE_PERCENT,'accuracy_metric':'monthly_regular_revenue_wape',
        'regular':{'status':'not_evaluated'},'bulk':{'status':'not_evaluated'},'combined':{},
        'metrics':{},'baselines':{},'historical_comparison':[],'planning_projection':None,
        'data_end':through,'stage':'pattern_investigation',
        'limitations':'The below-15% target applies to regular monthly revenue, not total sales or feeding. No replacement accuracy has been established.',
        'warnings':['Historical ledger coverage must be reviewed before production publication.',
                    f'{snapshot.excluded_demo_count} generated demonstration invoices excluded.'],
    }
    if latest is None:
        return payload
    findings = latest.findings
    payload.update(stage='monthly_model_learning', model_id=latest.pk,
        status=model_status, quality_status='approved' if model_status=='ready' else 'withheld',
        model_status=model_status, method=findings.get('selected_candidate','unavailable'),
        model_family=findings.get('model_family'),
        available_periods=['monthly'], quality_gate=gate or {},
        metrics={'retrospective_regular_monthly':findings.get('selected_test', {}),
                 'prospective_regular_monthly':gate['prospective']['metrics'] if gate else {}},
        baselines={'selected':findings.get('selected_baseline'), 'retrospective_test':findings.get('baseline_test',{})},
        regular={'status':model_status}, bulk={'status':'not_modelled'},
        data_provenance={'model_cutoff':latest.cutoff,'model_source_revision':latest.source_revision,
                         'current_source_revision':snapshot.revision,'trained_through':latest.trained_through},
        limitations='One-month-ahead regular dairy revenue only. Historical scores are retrospective research; feeding and longer horizons are not validated. Error bands are not calibrated probability intervals.')
    messages = {
        'stale_data':'A SARIMA research model is fitted, but actual sales stop before the month needed for a current forecast. Demo data is excluded.',
        'refresh_required':'Actual sales or coverage changed; run offline model maintenance before using a forecast.',
        'research_only':'A fitted research model is available; coverage and prospective accuracy gates have not cleared publication.',
        'ready':'Approved one-month-ahead forecast for regular dairy revenue; feeding is excluded.',
    }
    payload['status_message']=messages.get(model_status,'More complete regular-sales history is required to train a validated model.')
    payload['warnings'].extend(gate['reasons'] if gate else ['Offline retraining required.'])
    if period != 'monthly':
        payload.update(status='unsupported_horizon',status_message='The replacement currently evaluates monthly regular revenue only. Weekly and yearly forecasts are withheld.')
        return payload
    if model_status == 'ready':
        payload['forecast']=[issued_point(run, issued)]
        payload['regular'].update(predicted_revenue=issued.value)
    return payload


def patterns_report():
    snapshot=load_snapshot()
    study=PatternStudy.objects.filter(analyzer_version=ANALYZER_VERSION,
                    source_revision=snapshot.revision,as_of=snapshot.as_of).first()
    return {'status':'current' if study else 'refresh_required','stage':'pattern_investigation',
            'source_revision':snapshot.revision,'findings':study.findings if study else None}
