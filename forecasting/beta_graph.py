"""Centered, three-series beta forecasts. Fitting occurs only in offline refresh.

Real and simulated outcomes share a display contract, never a training source.
Reconstructed past predictions and genuinely issued forecasts retain their basis.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import math
import numpy as np

from django.db import transaction
from django.conf import settings
from django.utils import timezone

from sales.models import Transaction
from .benchmarks import score
from .data import load_snapshot
from .learning import ENGINE_VERSION, shift_month
from .lifecycle import _commit_lock
from .models import GraphForecastRun, GraphPrediction, MonthlyModelRun
from .provisional import digest, series, train, fit, future_rows, project
from .seasonal import is_sarima_artifact
from .chart import period_start, period_end

VERSION = 'centered-beta-sarima-v3'
PERIODS = ('weekly', 'monthly', 'yearly')
SCOPES = ('real', 'demo2026')
VIEWS = ('combined', 'regular', 'feeding')
DEMO_ORIGIN = date(2026, 1, 1)
LABELS = {'combined': 'Combined', 'regular': 'Regular sales', 'feeding': 'Milk feeding'}


def advance(day, period, n):
    return (day+timedelta(weeks=n) if period == 'weekly' else
            date(day.year+n, 1, 1) if period == 'yearly' else shift_month(day, n))


def window(period, as_of):
    current = period_start(as_of, period)
    radius = 2 if period == 'yearly' else 3
    return [advance(current, period, n) for n in range(-radius, radius+1)]


def _primary(snapshot, rows):
    """Reuse a saved primary seasonal SARIMA only for its exact earlier history."""
    # The primary engine trains on the latest contiguous monthly block.
    # Older isolated sales must not make a matching saved seasonal fit unusable.
    # Keep the full source revision check; only its training window is narrower.
    last_gap = max((i for i, row in enumerate(rows) if row['actual'] is None), default=-1)
    rows = rows[last_gap+1:]
    if not rows:
        return None
    for run in MonthlyModelRun.objects.filter(engine_version=ENGINE_VERSION,
            source_revision=snapshot.revision, trained_through__lt=snapshot.as_of,
            status='research_candidate'):
        artifact = run.findings.get('artifact') or {}
        try:
            values, scale = artifact['normalized_history'], float(artifact['scale'])
            valid = (is_sarima_artifact(artifact) and digest(artifact) == run.findings.get('artifact_sha256')
                     and artifact['name'] == run.findings.get('selected_candidate')
                     and artifact['trained_through'] == rows[-1]['date'] and len(values) == len(rows)
                     and math.isfinite(scale) and scale > 0
                     and all(math.isfinite(float(p)) for p in artifact['params'])
                     and all(math.isclose(float(v)*scale, r['actual'], rel_tol=0, abs_tol=.005)
                             for v, r in zip(values, rows)))
            if not valid:
                continue
            return {'name': artifact['name'], 'specification': {k: artifact[k] for k in ('order', 'seasonal_order', 'trend')},
                    'scale': scale, 'values': values, 'parameters': artifact['params'],
                    'training_start': rows[0]['date'], 'trained_through': artifact['trained_through']}
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    return None


def context(period, scope, as_of):
    if period not in PERIODS or scope not in SCOPES:
        raise ValueError('Use weekly/monthly/yearly and real/demo2026.')
    if scope == 'demo2026' and settings.IS_PRODUCTION:
        raise ValueError('Demo forecasting is unavailable in production.')
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise ValueError('as_of must be a date.')
    if not 3 <= as_of.year <= 9997:
        raise ValueError('as_of is outside the supported calendar.')
    if scope == 'real' and as_of > timezone.localdate():
        raise ValueError('Real sales cannot use a future as_of date.')
    if scope == 'demo2026' and as_of < DEMO_ORIGIN:
        raise ValueError('The demo starts in 2026.')
    calendar = window(period, as_of)
    unit = 'weekly' if period == 'weekly' else 'monthly'
    current = period_start(as_of, period)
    snapshots = {}
    inputs = {}
    for target in calendar:
        cutoff = target if target <= current else (current if period != 'yearly' else as_of.replace(day=1))
        if scope == 'demo2026':
            cutoff = min(cutoff, period_start(DEMO_ORIGIN, unit))
        if cutoff not in snapshots:
            snapshots[cutoff] = load_snapshot(cutoff)
        snap = snapshots[cutoff]
        for segment in ('regular', 'feeding'):
            rows = series(snap, segment, unit)
            primary = _primary(snap, rows) if segment == 'regular' and unit == 'monthly' else None
            signature = digest({'version': VERSION, 'unit': unit, 'segment': segment, 'rows': rows, 'primary': primary})
            inputs[(target, segment)] = {'rows': rows, 'primary': primary, 'signature': signature}
    if scope == 'real':
        displayed = load_snapshot(as_of+timedelta(days=1))
        sales = [(s.id, s.day, s.segment, s.revenue) for s in displayed.sales]
        display_revision = displayed.revision
    else:
        sales = []
        for row in Transaction.objects.filter(source_reference__regex=r'^2026:[0-9]{6}$', is_voided=False).order_by('pk').values(
                'pk', 'created_at', 'source_sales_segment', 'total_amount'):
            day = timezone.localtime(row['created_at']).date()
            if day.year == 2026 and day <= as_of:
                sales.append((row['pk'], day, row['source_sales_segment'], row['total_amount']))
        display_revision = digest([(pk, day.isoformat(), segment, str(value)) for pk, day, segment, value in sales])
    identity = {'version': VERSION, 'period': period, 'scope': scope, 'as_of': as_of.isoformat(),
                'training_sources': {day.isoformat(): snap.revision for day, snap in snapshots.items()},
                'inputs': {f'{target}:{segment}': item['signature'] for (target, segment), item in inputs.items()},
                'display': display_revision}
    return {'period': period, 'scope': scope, 'as_of': as_of, 'calendar': calendar, 'unit': unit,
            'inputs': inputs, 'sales': sales, 'revision': digest(identity)}


def _point(component, target, period):
    if not component.get('state'):
        return None
    try:
        value = future_rows(component, [target.isoformat()], period)[0]['predicted']
        return value if math.isfinite(value) and 0 <= value < 1e18 else None
    except (ValueError, KeyError, ArithmeticError, np.linalg.LinAlgError):
        return None


def _train(rows, unit):
    observed = sum(row['actual'] is not None for row in rows)
    if 2 <= observed < 6:
        try:
            return {'state': fit(rows, 'local_level', 52 if unit == 'weekly' else 12),
                    'status': 'provisional', 'observed_periods': observed}
        except (ValueError, ArithmeticError, np.linalg.LinAlgError):
            return {'status': 'insufficient_data'}
    result = train(rows, unit)
    validation = [row for candidate in result.get('validation', {}).values()
                  for row in candidate.get('rows', [])]
    if (result.get('status') == 'fit_failed' and validation
            and all(row['actual'] == 0 for row in validation)):
        # Percentage error cannot rank candidates against a zero denominator.
        # Retain a fitted beta baseline, without claiming validated accuracy.
        try:
            state = fit(rows, 'local_level', 52 if unit == 'weekly' else 12)
            project(state, 1)
            return {**result, 'status': 'provisional', 'state': state,
                    'selection': 'local_level_zero_validation_denominator',
                    'observed_periods': observed}
        except (ValueError, ArithmeticError, np.linalg.LinAlgError):
            pass
    return result


def _issued_predictions(period, target):
    target_time = timezone.make_aware(datetime.combine(target, time.min))
    records = list(GraphPrediction.objects.filter(run__scope='real', run__period=period,
        target=target, issued_at__lt=target_time).values('run_id', 'view', 'value'))
    bundles = defaultdict(dict)
    for record in records:
        bundles[record['run_id']][record['view']] = record['value']
    # Prefer the most recent complete issue across all views. Picking the newest
    # component independently can mix refreshes and break Combined = R + F.
    for values in bundles.values():
        if (all(view in values for view in VIEWS)
                and abs(values['combined']-values['regular']-values['feeding']) <= Decimal('0.01')):
            return {view: float(values[view]) for view in VIEWS}
    values = {}
    for record in records:
        if record['view'] in ('regular', 'feeding'):
            values.setdefault(record['view'], float(record['value']))
    if values:
        # These component issues remain useful, but no coherent Combined issue
        # exists. Do not relabel a newly computed sum as an original forecast.
        values['combined'] = None
    return values


def build(ctx, artifacts=None):
    artifacts = dict(artifacts or {})
    period, scope, as_of = ctx['period'], ctx['scope'], ctx['as_of']
    current = period_start(as_of, period)
    totals = defaultdict(dict)
    for _, day, segment, amount in ctx['sales']:
        if segment in ('regular', 'feeding'):
            bucket = period_start(day, period)
            totals[bucket][segment] = totals[bucket].get(segment, Decimal('0'))+amount
    views = {}
    predictions = {}
    issued = {target: _issued_predictions(period, target) for target in ctx['calendar']
              if scope == 'real' and target <= current}
    for target in ctx['calendar']:
        for segment in ('regular', 'feeding'):
            item = ctx['inputs'][(target, segment)]
            signature = item['signature']
            if signature not in artifacts:
                artifacts[signature] = ({'state': item['primary'], 'status': 'provisional'} if item['primary']
                                        else _train(item['rows'], ctx['unit']))
            predictions[(target, segment)] = _point(artifacts[signature], target, period)
        values = [predictions[(target, segment)] for segment in ('regular', 'feeding')]
        predictions[(target, 'combined')] = round(sum(values), 2) if all(v is not None for v in values) else None
    for view in VIEWS:
        points, evaluations = [], defaultdict(list)
        for target in ctx['calendar']:
            actuals = totals.get(target, {})
            actual = (sum(actuals.values(), Decimal('0')) if view == 'combined' and actuals else actuals.get(view))
            predicted = predictions[(target, view)]
            partial = target == current or (scope == 'demo2026' and actual is not None and
                (target < DEMO_ORIGIN or period_end(target, period) >= date(2027, 1, 1)))
            basis = 'fixed_origin_demo' if scope == 'demo2026' else 'historical_test' if target < current else 'forecast'
            if view in issued.get(target, {}):
                predicted = issued[target][view]
                if predicted is not None:
                    basis = 'issued_forecast'
            if target < current and not partial and actual is not None and predicted is not None:
                evaluations[basis].append({'actual': float(actual), 'predicted': predicted})
            points.append({'date': target.isoformat(), 'end_date': period_end(target, period).isoformat(),
                           'actual': float(actual) if actual is not None else None,
                           'past_prediction': predicted if target < current else None,
                           'future_prediction': predicted if target >= current else None,
                           'prediction_basis': basis, 'is_current': target == current,
                           'is_partial_period': partial})
        views[view] = {'label': LABELS[view], 'points': points,
                       'evaluation': {basis: score(rows) for basis, rows in evaluations.items()},
                       'message': None if any(p['past_prediction'] is not None or p['future_prediction'] is not None
                                              for p in points) else 'Not enough sales history yet.'}
    return {'scope': scope, 'is_demo': scope == 'demo2026', 'training_scope': 'real', 'period': period,
            'as_of': as_of.isoformat(), 'available_periods': list(PERIODS), 'available_views': list(VIEWS),
            'range': {'start_date': ctx['calendar'][0].isoformat(), 'end_date': period_end(ctx['calendar'][-1], period).isoformat(),
                      'past_periods': 2 if period == 'yearly' else 3, 'future_periods': 2 if period == 'yearly' else 3},
            'views': views, 'artifacts': {item['signature']: artifacts[item['signature']] for item in ctx['inputs'].values()}}


def checked(findings):
    data = dict(findings)
    checksum = data.pop('checksum', None)
    return data if checksum == digest(data) else None


def refresh(period='monthly', scope='real', as_of=None):
    as_of = as_of or timezone.localdate()
    ctx = context(period, scope, as_of)
    lookup = {'engine_version': VERSION, 'scope': scope, 'period': period,
              'as_of': as_of, 'source_revision': ctx['revision']}
    existing = GraphForecastRun.objects.filter(**lookup).first()
    if existing:
        if checked(existing.findings) is None:
            raise ValueError('Saved graph checksum failed.')
        return existing, False
    previous = GraphForecastRun.objects.filter(engine_version=VERSION, scope=scope, period=period).first()
    prior = checked(previous.findings) if previous else None
    findings = build(ctx, prior.get('artifacts', {}) if prior else {})
    findings['checksum'] = digest(findings)
    with transaction.atomic():
        _commit_lock()
        if context(period, scope, as_of)['revision'] != ctx['revision']:
            raise ValueError('Sales changed during fitting; retry refresh.')
        run, created = GraphForecastRun.objects.get_or_create(**lookup, defaults={'findings': findings})
        published = checked(run.findings)
        if published is None:
            raise ValueError('Saved graph checksum failed.')
        if scope == 'real' and as_of == timezone.localdate():
            for view, result in published['views'].items():
                for point in result['points']:
                    target = date.fromisoformat(point['date'])
                    if (target > as_of and timezone.make_aware(datetime.combine(target, time.min)) > timezone.now()
                            and point['future_prediction'] is not None):
                        GraphPrediction.objects.get_or_create(run=run, view=view, target=target,
                                                             defaults={'value': str(point['future_prediction'])})
    return run, created


def report(period='monthly', scope='real', as_of=None):
    ctx = context(period, scope, as_of or timezone.localdate())
    run = GraphForecastRun.objects.filter(engine_version=VERSION, scope=scope, period=period,
                                        as_of=ctx['as_of'], source_revision=ctx['revision']).first()
    data = checked(run.findings) if run else None
    if data is None:
        # The GET fallback exposes actuals/calendar only and never invokes fit.
        empty = dict(ctx)
        empty['inputs'] = {key: {**item, 'signature': 'unavailable'} for key, item in ctx['inputs'].items()}
        data = build(empty, {'unavailable': {'status': 'unavailable'}})
    data = {key: value for key, value in data.items() if key != 'artifacts'}
    return {**data, 'status': 'ready' if run and checked(run.findings) else 'refresh_required',
            'model_id': run.pk if run else None}
