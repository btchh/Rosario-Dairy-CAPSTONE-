"""Offline SARIMA projections for all chart views; never production approval.

Unknown invoice periods stay NaN in the state-space model. Future years are
single-origin monthly paths, not sums of rolling retrospective predictions.
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import math
import warnings

import numpy as np
from django.db import transaction
from django.utils import timezone
from statsmodels.tsa.statespace.sarimax import SARIMAX
from threadpoolctl import threadpool_limits

from .data import load_snapshot
from .learning import ENGINE_VERSION, MIN_MONTHS, month_end, shift_month
from .lifecycle import _commit_lock, valid_coverage, coverage_revision, coverage_rows
from .models import MonthlyModelRun, ProvisionalForecastRun
from .seasonal import is_sarima_artifact
from .graph_sarima import regular_weekly_component, regular_weekly_dependency

VERSION = 'actual-provisional-sarima-v4'
PERIODS = ('weekly', 'monthly', 'yearly')
VIEWS = ('regular', 'feeding', 'combined')


def start(day, period):
    return day - timedelta(days=day.weekday()) if period == 'weekly' else day.replace(day=1)


def advance(day, period, steps=1):
    return day + timedelta(weeks=steps) if period == 'weekly' else shift_month(day, steps)


def series(snapshot, segment, period):
    totals = defaultdict(Decimal)
    for sale in snapshot.sales:
        if sale.segment == segment:
            totals[start(sale.day, period)] += sale.revenue
    rows = []
    if totals:
        day = min(totals)
        while day <= max(totals):
            rows.append({'date': day.isoformat(), 'actual': float(totals[day]) if day in totals else None})
            day = advance(day, period)
    return rows


def digest(value):
    def canonical(item):
        if isinstance(item, float) and item == 0:
            return 0.0
        if isinstance(item, dict):
            return {k: canonical(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [canonical(v) for v in item]
        return item
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, allow_nan=False).encode()).hexdigest()


def primary_monthly(snapshot):
    """Adapt the exact-source primary fit without optimizing or relaxing approval.

    Short/gapped history can use the sparse-data component. Eligible history
    awaits primary maintenance instead of silently selecting another model.
    """
    coverage = valid_coverage(snapshot)
    rows = coverage_rows(snapshot, coverage)
    coverage_hash = coverage_revision(coverage)
    run = MonthlyModelRun.objects.filter(engine_version=ENGINE_VERSION,
        source_revision=snapshot.revision, coverage_revision=coverage_hash, cutoff=snapshot.as_of).first()
    artifact = (run.findings.get('artifact') or {}) if run else {}
    try:
        actual_hash = digest(artifact)
    except (ValueError, TypeError):
        actual_hash = 'invalid'
    dependency = {'model_id': run.pk if run else None, 'coverage_revision': coverage_hash,
                  'artifact_hash': actual_hash, 'status': run.status if run else 'missing',
                  'selected_candidate': run.findings.get('selected_candidate') if run else None,
                  'trained_through': run.trained_through.isoformat() if run and run.trained_through else None,
                  'declared_hash': run.findings.get('artifact_sha256') if run else None}
    base = {'dependency': dependency, 'primary_model_id': run.pk if run else None,
            'primary_engine_version': ENGINE_VERSION, 'model_basis': 'primary_monthly_sarima'}
    if run is None or run.status != 'research_candidate':
        if len(rows) < MIN_MONTHS:
            return {**base, 'status': 'fallback_allowed', 'model_basis': 'provisional_fallback',
                    'fallback_reason': 'primary_requires_36_contiguous_completed_months'}
        return {**base, 'status': 'primary_refresh_required' if run is None else 'primary_model_unavailable'}
    try:
        history = artifact['normalized_history']
        scale = float(artifact['scale'])
        valid = (run.findings.get('status') == 'research_candidate' and
                 is_sarima_artifact(artifact) and actual_hash == run.findings.get('artifact_sha256') and
                 artifact['name'] == run.findings.get('selected_candidate') and len(rows) >= MIN_MONTHS and
                 len(rows) == len(history) and artifact['trained_through'] == rows[-1]['month'] and
                 run.trained_through == month_end(date.fromisoformat(rows[-1]['month'])) and
                 math.isfinite(scale) and scale > 0 and
                 all(math.isfinite(float(p)) for p in artifact['params']) and
                 all(math.isclose(float(v)*scale, r['revenue'], rel_tol=0, abs_tol=.005)
                     for r, v in zip(rows, history)))
    except (KeyError, ValueError, TypeError, OverflowError):
        valid = False
    if not valid:
        return {**base, 'status': 'invalid_primary_artifact'}
    state = {'name': artifact['name'], 'specification': {k: artifact[k] for k in ('order', 'seasonal_order', 'trend')},
             'scale': artifact['scale'], 'values': history, 'parameters': artifact['params'],
             'training_start': rows[0]['month'], 'trained_through': artifact['trained_through']}
    return {**base, 'status': 'provisional', 'state': state, 'seasonal_terms_enabled': True,
            'selection': 'reuse_primary_earlier_validation_sarima_selection',
            'observed_periods': len(rows), 'missing_periods': 0,
            'verified_zero_periods': sum(c.confirmed_zero for c in coverage.values()
                if rows[0]['month'] <= c.month.isoformat() <= rows[-1]['month']),
            # Rolling one-month scores cannot describe a multi-step path.
            'test': None, 'primary_monthly_backtest': {'basis': 'rolling_one_month_ahead',
                                                     'metrics': run.findings.get('selected_test')}}


def context(today=None):
    today = today or timezone.localdate()
    if today > timezone.localdate():
        raise ValueError('Future extraction dates are not allowed.')
    snapshots = {p: load_snapshot(start(today, p)) for p in ('weekly', 'monthly')}
    targets = {
        'weekly': [advance(start(today, 'weekly'), 'weekly', n).isoformat() for n in range(1, 14)],
        'monthly': [shift_month(today.replace(day=1), n).isoformat() for n in range(1, 13)],
        'yearly': [date(today.year+n, 1, 1).isoformat() for n in (1, 2)],
    }
    primary = primary_monthly(snapshots['monthly'])
    weekly = regular_weekly_dependency(snapshots['weekly'])
    identity = {'version': VERSION, 'sources': {p: s.revision for p, s in snapshots.items()}, 'targets': targets,
                'primary_monthly': {'dependency': primary['dependency'], 'status': primary['status']},
                'primary_weekly': _weekly_identity(weekly)}
    return snapshots, targets, digest(identity)


def _weekly_identity(dependency):
    # The target calendar already versions the bundle. With unchanged history,
    # an advancing extraction cutoff alone does not require fitting a component.
    if dependency['status'] == 'insufficient_history':
        # The fallback trains on provisional.series, whose rows already version
        # the component. Unused strict boundary weeks must not force a refit.
        return {'status': 'insufficient_history'}
    return {'status': dependency['status'], 'rows': dependency['rows'],
            'dependency': {key: value for key, value in dependency['dependency'].items()
                           if key != 'snapshot_cutoff'}}


def specification(name, season):
    if name == 'local_level':
        return {'order': (0, 1, 0), 'seasonal_order': (0, 0, 0, 0), 'trend': 'n'}
    return {'order': (1, 0, 0), 'seasonal_order': (1, 0, 0, season) if name == 'seasonal_ar' else (0, 0, 0, 0), 'trend': 'c'}


def model_for(values, spec):
    return SARIMAX(np.asarray([np.nan if v is None else v for v in values]),
                   **spec, enforce_stationarity=True, enforce_invertibility=True)


def fit(rows, name, season):
    observed = [r['actual'] for r in rows if r['actual'] is not None]
    scale = max(float(np.mean(np.abs(observed))), 1.0)
    values = [r['actual']/scale if r['actual'] is not None else None for r in rows]
    spec = specification(name, season)
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter('ignore')
        model = model_for(values, spec)
        result = model.fit(disp=False, maxiter=150)
        if not result.mle_retvals.get('converged'):
            result = model.fit(method='powell', disp=False, maxiter=150)
    if not result.mle_retvals.get('converged') or not np.isfinite(result.params).all() or not math.isfinite(result.llf):
        raise ValueError('SARIMA fit did not converge to finite parameters.')
    return {'name': name, 'specification': spec, 'scale': scale, 'values': values,
            'parameters': [float(x) for x in result.params], 'training_start': rows[0]['date'],
            'trained_through': rows[-1]['date']}


def project(state, steps):
    if steps <= 0 or steps > 1200:
        raise ValueError('Unsupported projection length.')
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter('ignore')
        result = model_for(state['values'], state['specification']).filter(state['parameters'])
        future = result.get_forecast(steps=steps)
        values = np.asarray(future.predicted_mean)*state['scale']
        bounds = np.asarray(future.conf_int(alpha=.1))*state['scale']
    rows = []
    for value, (lower, upper) in zip(values, bounds):
        if not all(math.isfinite(float(x)) and abs(x) < 1e18 for x in (value, lower, upper)):
            raise ValueError('Non-finite or unsupported forecast value.')
        value = max(0., float(value))
        rows.append({'predicted': round(value, 2), 'lower_bound': round(max(0., min(value, float(lower))), 2),
                     'upper_bound': round(max(value, float(upper)), 2)})
    return rows


def evaluate(state, rows, period):
    origin = date.fromisoformat(state['trained_through'])
    last = date.fromisoformat(rows[-1]['date'])
    steps = (last-origin).days//7 if period == 'weekly' else (last.year-origin.year)*12+last.month-origin.month
    path = project(state, steps)
    comparisons = []
    for row in rows:
        if row['actual'] is None:
            continue
        target = date.fromisoformat(row['date'])
        step = (target-origin).days//7 if period == 'weekly' else (target.year-origin.year)*12+target.month-origin.month
        comparisons.append({'date': row['date'], 'actual': row['actual'], 'predicted': path[step-1]['predicted'],
                            'trained_through': origin.isoformat()})
    denominator = sum(abs(r['actual']) for r in comparisons)
    return {'wape': round(100*sum(abs(r['actual']-r['predicted']) for r in comparisons)/denominator, 4)
            if denominator else None, 'sample_count': len(comparisons), 'rows': comparisons,
            'basis': 'single_origin_observed_periods_only'}


def train(rows, period):
    observed = [i for i, r in enumerate(rows) if r['actual'] is not None]
    if len(observed) < 6:
        return {'status': 'insufficient_data', 'minimum_observed_periods': 6}
    holdout = min(6, max(1, len(observed)//5))
    validation_index, test_index = observed[-2*holdout], observed[-holdout]
    season = 52 if period == 'weekly' else 12
    candidates = ['local_level', 'ar1']
    if validation_index >= 2*season and sum(r['actual'] is not None for r in rows[:validation_index]) >= 2*season:
        candidates.append('seasonal_ar')
    results = {}
    for name in candidates:
        try:
            state = fit(rows[:validation_index], name, season)
            results[name] = evaluate(state, rows[validation_index:test_index], period)
        except (ValueError, np.linalg.LinAlgError) as exc:
            results[name] = {'error': str(exc), 'wape': None}
    ranked = sorted((n for n in candidates if results[n].get('wape') is not None), key=lambda n: results[n]['wape'])
    failures = []
    # Freeze the validation winner before inspecting any test-period outcomes.
    # A test/refit failure invalidates that winner; it must not select a runner-up.
    if not ranked:
        return {'status': 'fit_failed', 'validation': results, 'fit_failures': failures,
                'selected_candidate': None}
    name = ranked[0]
    try:
        test_state = fit(rows[:test_index], name, season)
        test = evaluate(test_state, rows[test_index:], period)
        state = fit(rows, name, season)
        project(state, 1)
        return {'status': 'provisional', 'state': state, 'validation': results, 'test': test,
                'selection': 'earlier_validation_only; test_not_used_for_selection',
                'fit_failures': failures, 'seasonal_terms_enabled': name == 'seasonal_ar',
                'observed_periods': len(observed), 'missing_periods': len(rows)-len(observed)}
    except (ValueError, np.linalg.LinAlgError) as exc:
        failures.append({'candidate': name, 'error': str(exc)})
    return {'status': 'fit_failed', 'validation': results, 'fit_failures': failures,
            'selected_candidate': name}


def future_rows(component, targets, period):
    state = component.get('state')
    if not state:
        return []
    origin = date.fromisoformat(state['trained_through'])
    end = date.fromisoformat(targets[-1])
    end = end.replace(month=12) if period == 'yearly' else end
    unit = 'weekly' if period == 'weekly' else 'monthly'
    steps = (end-origin).days//7 if unit == 'weekly' else (end.year-origin.year)*12+end.month-origin.month
    path = {advance(origin, unit, n).isoformat(): row for n, row in enumerate(project(state, steps), 1)}
    rows = []
    for target in targets:
        if period == 'yearly':
            year = date.fromisoformat(target)
            parts = [path[year.replace(month=m).isoformat()] for m in range(1, 13)]
            point = {key: round(sum(p[key] for p in parts), 2) for key in ('predicted', 'lower_bound', 'upper_bound')}
        else:
            point = path[target]
        rows.append({'date': target, **point, 'is_provisional': True, 'production_approved': False,
                     'trained_through': state['trained_through'],
                     'basis': 'single_origin_monthly_path_sum' if period == 'yearly' else 'single_origin_sarima',
                     'interval_basis': 'sum_of_uncalibrated_monthly_bounds' if period == 'yearly' else 'uncalibrated_90_percent_model_interval'})
    return rows


def build(snapshots, targets, previous=None):
    components = {}
    primary = primary_monthly(snapshots['monthly'])
    weekly = regular_weekly_dependency(snapshots['weekly'])
    for period in ('weekly', 'monthly'):
        for segment in ('regular', 'feeding'):
            key = f'{segment}_{period}'
            rows = series(snapshots[period], segment, period)
            is_primary = segment == 'regular' and period == 'monthly'
            is_weekly = segment == 'regular' and period == 'weekly'
            signature = (digest({'rows': rows, 'primary': primary}) if is_primary else
                         digest({'rows': rows, 'primary': _weekly_identity(weekly)}) if is_weekly else digest(rows))
            old = (previous or {}).get('components', {}).get(key, {})
            if is_weekly and weekly['status'] != 'insufficient_history':
                component = (old if old.get('input_signature') == signature and old.get('state')
                             else regular_weekly_component(weekly))
            elif is_primary and primary['status'] != 'fallback_allowed':
                component = primary
            else:
                component = old if old.get('input_signature') == signature and old.get('state') else train(rows, period)
                component = {**component, 'model_basis': 'provisional_fallback' if is_primary or is_weekly else 'provisional_selection'}
                if is_primary:
                    component['fallback_reason'] = primary['fallback_reason']
                elif is_weekly:
                    component['fallback_reason'] = 'annual_weekly_sarima_requires_130_contiguous_completed_weeks'
            components[key] = {**component, 'input_signature': signature}
    periods = {}
    for period in PERIODS:
        views = {}
        for segment in ('regular', 'feeding'):
            component = components[f'{segment}_{"monthly" if period == "yearly" else period}']
            points = future_rows(component, targets[period], period)
            views[segment] = {'status': 'provisional' if points else component['status'], 'points': points,
                'model': component.get('state', {}).get('name'),
                'seasonal_terms_enabled': component.get('seasonal_terms_enabled', False),
                'model_basis': component.get('model_basis'),
                'primary_model_id': component.get('primary_model_id'),
                'primary_engine_version': component.get('primary_engine_version'),
                'primary_monthly_backtest': component.get('primary_monthly_backtest') if period == 'monthly' else None,
                'primary_weekly_backtest': component.get('primary_weekly_backtest') if period == 'weekly' else None,
                'fallback_reason': component.get('fallback_reason'),
                'metrics': component.get('test') if period != 'yearly' else None,
                'observed_periods': component.get('observed_periods'), 'missing_periods': component.get('missing_periods'),
                'warnings': ['Limited actual invoices; accuracy is unvalidated for future periods.',
                    'Missing invoice periods are unknown observations, not zero sales.',
                    'Bounds are uncalibrated model uncertainty; they do not cover all business risks.']}
            if component.get('fallback_reason'):
                frequency = 'weekly' if period == 'weekly' else 'monthly'
                views[segment]['warnings'].append(f'Short or gapped regular history uses the provisional fallback, not the primary {frequency} model.')
        combined = []
        if views['regular']['points'] and views['feeding']['points']:
            for regular, feeding in zip(views['regular']['points'], views['feeding']['points']):
                combined.append({'date': regular['date'], **{k: float(Decimal(str(regular[k]))+Decimal(str(feeding[k])))
                    for k in ('predicted', 'lower_bound', 'upper_bound')},
                    'is_provisional': True, 'production_approved': False, 'basis': 'sum_of_component_predictions',
                    'interval_basis': 'sum_of_uncalibrated_component_bounds'})
        views['combined'] = {'status': 'provisional' if combined else 'component_unavailable', 'points': combined,
                              'component_models': {s: {k: views[s].get(k) for k in
                                  ('model', 'model_basis', 'primary_model_id', 'primary_engine_version')} for s in ('regular', 'feeding')},
                              'metrics': None, 'warnings': ['Regular plus feeding; unclassified invoices excluded.',
                                'Summed component bounds are not a calibrated combined confidence interval.']}
        periods[period] = views
    return {'version': VERSION, 'components': components, 'periods': periods, 'targets': targets,
            'source_revisions': {p: s.revision for p, s in snapshots.items()},
            'cutoffs': {p: s.as_of.isoformat() for p, s in snapshots.items()}}


def refresh(today=None):
    snapshots, targets, revision = context(today)
    existing = ProvisionalForecastRun.objects.filter(engine_version=VERSION, source_revision=revision).first()
    if existing:
        return existing, False
    previous = ProvisionalForecastRun.objects.filter(engine_version=VERSION).order_by('-pk').first()
    findings = build(snapshots, targets, previous.findings if previous else None)
    findings['artifact_digest'] = digest(findings)
    with transaction.atomic():
        _commit_lock()
        if context(today)[2] != revision:
            raise ValueError('Actual source or target periods changed during fitting; retry refresh.')
        return ProvisionalForecastRun.objects.get_or_create(engine_version=VERSION, source_revision=revision,
                                                            defaults={'findings': findings})


def report(period, today=None):
    snapshots, targets, revision = context(today)
    run = ProvisionalForecastRun.objects.filter(engine_version=VERSION, source_revision=revision).first()
    if not run:
        return {'status': 'refresh_required', 'engine_version': VERSION, 'views': {}, 'targets': targets[period]}
    findings = dict(run.findings)
    checksum = findings.pop('artifact_digest', None)
    if checksum != digest(findings):
        return {'status': 'invalid_artifact', 'engine_version': VERSION, 'views': {}, 'targets': targets[period]}
    views = findings['periods'][period]
    current = today or timezone.localdate()
    for segment in ('regular', 'feeding'):
        rows = views[segment]['points']
        if rows:
            age = (current-date.fromisoformat(rows[0]['trained_through'])).days
            views[segment]['training_age_days'] = age
            views[segment]['stale_history'] = age > (14 if period == 'weekly' else 62)
            if views[segment]['stale_history']:
                views[segment]['warnings'].append('History is stale; projection crosses unobserved periods before the target.')
    combined = views['combined']
    combined['stale_history'] = any(views[s].get('stale_history', False) for s in ('regular', 'feeding'))
    combined['component_training_origins'] = {s: views[s]['points'][0]['trained_through']
        for s in ('regular', 'feeding') if views[s]['points']}
    if combined['stale_history']:
        combined['warnings'].append('Component history is stale; these are rough projections across unobserved periods.')
    return {'status': 'provisional', 'engine_version': VERSION, 'model_id': run.pk, 'views': views, 'targets': targets[period],
            'source_revisions': findings['source_revisions'], 'production_approved': False}
