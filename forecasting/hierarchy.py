"""Offline item forecasts, honest sparse-history states, and coherent unit rollups."""
from collections import defaultdict
from datetime import timedelta
from statistics import mean

import numpy as np
from django.db import transaction
from django.utils import timezone

from .hierarchy_source import load_source
from .models import HierarchyForecastRun
from .provisional import digest
from .learning import shift_month

VERSION = 'item-hierarchy-v8-sarima-only'
PERIODS = {'daily': (14, 7, 42), 'weekly': (8, 52, 20), 'monthly': (12, 12, 20)}


def start(day, period):
    return day if period == 'daily' else day-timedelta(days=day.weekday()) if period == 'weekly' else day.replace(day=1)


def advance(day, period, count=1):
    return shift_month(day, count) if period == 'monthly' else day+timedelta(days=count*(7 if period == 'weekly' else 1))


def cutoff_for(latest, period):
    boundary = start(latest, period)
    return advance(boundary, period) if advance(boundary, period)-timedelta(days=1) == latest else boundary


def covered_cutoff(source, period):
    """End training after the last ledger-covered completed period.

    Recent sales in an incomplete week/month must not turn an unobserved tail
    into training observations. Those calendar periods remain projection steps;
    missing periods within retained history are still explicit unknowns.
    """
    limit = cutoff_for(source['latest_day'], period)
    completed = {start(day, period) for day in source['coverage_days']
                 if advance(start(day, period), period) <= limit}
    return advance(max(completed), period) if completed else limit


def projection_start(source, period, as_of):
    return max(cutoff_for(source['latest_day'], period), start(as_of, period))


def run_revision(source, period, as_of):
    return digest({'source': source['revision'], 'projection_start': projection_start(source, period, as_of).isoformat()})


def predict(values, method, horizon, season):
    from .methods import forecast
    return forecast(values, method, horizon, season)


def fit_series(values, dates, targets, period, *, active=True):
    from .demand_analysis import classify
    from .methods import eligibility
    horizon, season, _ = PERIODS[period]
    known = [v for v in values if v is not None]
    nonzero = sum(v > 0 for v in known)
    empty = {'period': period, 'classification': classify(values, dates, period), 'status': 'insufficient_history', 'method': None, 'metrics': None,
        'supported_series': 0, 'total_series': 1,
        'training_start': dates[0].isoformat() if dates else None,
        'known_periods': len(known), 'nonzero_periods': nonzero, 'test': [],
        'history': [{'date': d.isoformat(), 'actual': v} for d, v in zip(dates[-24:], values[-24:])],
        'forecast': [{'date': d.isoformat(), 'value': None, 'lower': None, 'upper': None, 'steps_ahead': index+1}
                     for index, d in enumerate(targets)],
        'reason': 'At least six selling periods and enough covered history are required.'}
    if not active:
        return {**empty, 'status': 'inactive_item', 'reason': 'Item is inactive in the current catalog.'}
    if nonzero < 6:
        return {**empty, 'reason': 'SARIMA requires at least six nonzero recorded periods.'}
    test_count = 14 if period == 'daily' else 8
    validation_count = 7 if period == 'daily' else 4
    split = len(values)-test_count
    validation_start = split-validation_count
    if validation_start < 1:
        return {**empty, 'reason': 'SARIMA requires separate training, validation, and test periods.'}
    for label, history in (('full history', values),
                           ('earliest validation training window', values[:validation_start])):
        reason = eligibility(history, 'sarima', period)
        if reason:
            return {**empty, 'reason': f'SARIMA is unavailable for {label}: {reason}'}
    method = 'sarima'
    try:
        guessed = predict(values[:validation_start], method, validation_count, season)
        errors = [abs(v-p) for v, p in zip(values[validation_start:split], guessed) if v is not None]
        if len(errors) < max(2, validation_count//2):
            return {**empty, 'reason': 'SARIMA validation has too few observed target periods.'}
        held = predict(values[:split], method, test_count, season)
        # Advance the model through every intervening period. An old forecast
        # cannot simply be assigned a current date, especially for seasonal models.
        future = predict(values, method, len(targets), season)
    except (ValueError, ArithmeticError, np.linalg.LinAlgError) as error:
        return {**empty, 'status': 'fit_failed', 'method': method,
                'reason': f'SARIMA could not produce a valid saved forecast: {error}'}
    test = [{'date': d.isoformat(), 'actual': v, 'predicted': round(max(0, p), 2)}
            for d, v, p in zip(dates[split:], values[split:], held) if v is not None]
    errors = [r['actual']-r['predicted'] for r in test]
    low, high = (np.quantile(errors, [.1, .9]) if len(errors) >= 8 else (None, None))
    return {**empty, 'status': 'available', 'supported_series': 1, 'method': method, 'reason': None,
        'selection': 'SARIMA is the only operational model; earlier validation measures it and the later test does not select a model.',
        'candidates': {'sarima': mean(errors)}, 'test': test, 'metrics': metrics(test),
        'range_basis': 'Historical signed test-error quantiles apply only within the original forecast horizon. Longer projections have no validated error band.',
        'forecast': [{'date': d.isoformat(), 'value': round(max(0, float(p)), 2), 'steps_ahead': index+1,
            'lower': round(max(0, float(p)+float(low)), 2) if low is not None and index < horizon else None,
            'upper': round(max(0, float(p)+float(high)), 2) if high is not None and index < horizon else None}
            for index, (d, p) in enumerate(zip(targets, future))]}


def metrics(rows):
    from .evaluation import metrics as evaluate
    return evaluate(rows)


def rollup(parts):
    if not parts:
        return None
    supported = sum(p.get('supported_series', int(p['status'] == 'available')) for p in parts)
    total = sum(p.get('total_series', 1) for p in parts)
    history_dates = sorted({r['date'] for p in parts for r in p['history']})[-24:]
    actuals = [{r['date']: r['actual'] for r in p['history']} for p in parts]
    history = [{'date': day, 'actual': round(sum(m[day] for m in actuals if m.get(day) is not None), 2)
                if any(m.get(day) is not None for m in actuals) else None} for day in history_dates]
    forecasts = []
    for index, point in enumerate(parts[0]['forecast']):
        known = [p['forecast'][index]['value'] for p in parts if p['forecast'][index]['value'] is not None]
        forecasts.append({'date': point['date'], 'value': round(sum(known), 2) if known else None,
                          'lower': None, 'upper': None, 'steps_ahead': point.get('steps_ahead')})
    # Parent accuracy concerns the supported subset, not unknown item forecasts.
    tests = defaultdict(lambda: {'actual': 0.0, 'predicted': 0.0})
    for p in parts:
        for row in p.get('test', []):
            tests[row['date']]['actual'] += row['actual']
            tests[row['date']]['predicted'] += row['predicted']
    test = [{'date': day, **row} for day, row in sorted(tests.items())]
    from .demand_analysis import classify
    from datetime import date
    period = parts[0].get('period', 'monthly')
    classification = classify([r['actual'] for r in history], [date.fromisoformat(r['date']) for r in history], period)
    return {'period': period, 'classification': classification, 'status': 'available' if supported == total else 'partial' if supported else 'insufficient_history',
        'method': 'sum_of_item_forecasts', 'known_periods': None, 'nonzero_periods': None,
        'supported_series': supported, 'total_series': total, 'history': history,
        'forecast': forecasts, 'metrics': metrics(test), 'test': test,
        'reason': 'Some series remain unavailable; totals include model forecasts only.' if supported < total else None,
        'range_basis': 'Parent ranges are withheld; summing item quantiles would ignore dependence.'}


def node(node_id, name, items, *, category_id=None):
    units = sorted({item['unit'] for item in items if item.get('demand') is not None})
    return {'id': node_id, 'name': name, 'category_id': category_id,
            'item_count': len(items), 'demand': {unit: rollup([item['demand'] for item in items
                if item['unit'] == unit and item.get('demand') is not None]) for unit in units},
            'revenue': rollup([item['revenue'] for item in items if item.get('revenue') is not None])}


def build(source, period, as_of=None):
    as_of = as_of or timezone.localdate()
    horizon, _, _ = PERIODS[period]
    cutoff = covered_cutoff(source, period)
    projection_date = projection_start(source, period, as_of)
    targets = []
    target = cutoff
    while target < advance(projection_date, period, horizon):
        targets.append(target)
        target = advance(target, period)
    # Retain the original saved horizon for historical browsing and the whole
    # current display/horizon. Intermediate predictions need not bloat the API.
    retained = {advance(cutoff, period, n).isoformat() for n in range(horizon)}
    retained.update(advance(projection_date, period, n).isoformat() for n in range(-3, horizon))
    coverage = {start(day, period) for day in source['coverage_days'] if day < cutoff}
    contiguous_start = advance(cutoff, period, -1)
    if period != 'daily':
        while advance(contiguous_start, period, -1) in coverage:
            contiguous_start = advance(contiguous_start, period, -1)
    entries = defaultdict(lambda: defaultdict(lambda: {'quantity': 0.0, 'revenue': 0.0,
        'has_quantity': False, 'has_revenue': False, 'unknown_quantity': False, 'unknown_revenue': False}))
    for fact in source['facts']:
        if fact['day'] >= cutoff:
            continue
        row = entries[(fact['segment'], fact['product_id'])][start(fact['day'], period)]
        for metric in ('quantity', 'revenue'):
            if fact[metric] is not None:
                row[metric] += float(fact[metric]); row['has_'+metric] = True
            else:
                row['unknown_'+metric] = True
    views = {}
    for segment in ('regular', 'feeding'):
        items = []
        for (group, pid), values in entries.items():
            if group != segment:
                continue
            product = source['products'][pid]
            first = max(min(values), contiguous_start) if period != 'daily' else min(values)
            dates = []
            current = first
            while current < cutoff:
                dates.append(current); current = advance(current, period)
            item = {**product, 'demand': None, 'revenue': None}
            for metric, field in [('quantity', 'demand'), ('revenue', 'revenue')]:
                if not any(row['has_'+metric] for row in values.values()):
                    continue
                series = [None if day in values and values[day]['unknown_'+metric]
                          else round(values[day][metric], 2) if day in values and values[day]['has_'+metric]
                          else 0.0 if day in coverage else None for day in dates]
                item[field] = fit_series(series, dates, targets, period, active=product['is_active'])
                item[field]['forecast'] = [p for p in item[field]['forecast'] if p['date'] in retained]
            items.append(item)
        views[segment] = assemble(items)
    combined = []
    for pid in sorted({item['id'] for view in views.values() for item in view['items']}):
        parts = [item for view in views.values() for item in view['items'] if item['id'] == pid]
        item = {**source['products'][pid]}
        for field in ('demand', 'revenue'):
            present = [p[field] for p in parts if p[field] is not None]
            item[field] = rollup(present) if len(present) > 1 else present[0] if present else None
        combined.append(item)
    views['combined'] = assemble(combined)
    category_catalog = source.get('categories')
    if category_catalog is None:
        category_catalog = [{'id': cid, 'name': name} for cid, name in sorted({(p['category_id'], p['category_name']) for p in source['products'].values() if p['id'] != 0})]
    for view in views.values():
        present = {i['id'] for i in view['items']}
        view['items'].extend({**p, 'demand': None, 'revenue': None} for pid, p in source['products'].items() if pid != 0 and pid not in present)
        view['items'].sort(key=lambda i: (i['name'].casefold(), i['id']))
        present_categories = {c['id'] for c in view['categories']}
        view['categories'].extend({'id': c['id'], 'name': c['name'], 'item_count': 0, 'demand': {}, 'revenue': None} for c in category_catalog if c['id'] not in present_categories)
        for category in view['categories']:
            category['item_count'] = sum(i['category_id'] == category['id'] for i in view['items'])
        view['categories'].sort(key=lambda c: c['name'].casefold())
        view['overall']['item_count'] = len(view['items'])
    return {'engine_version': VERSION, 'period': period, 'horizon': horizon,
        'source_revision': source['revision'], 'projection_start': projection_date.isoformat(),
        'cutoff': cutoff.isoformat(), 'trained_through': (cutoff-timedelta(days=1)).isoformat(),
        'latest_recorded_sale': source['latest_day'].isoformat(), 'audit': source['audit'],
        'category_basis': 'Current reviewed catalog categories, applied consistently to retained history.',
        'calendar_basis': 'Models project from the last complete recorded period through the current Philippine calendar horizon. Unrecorded intervening periods are never added to training or treated as actual sales.',
        'views': views}


def assemble(items):
    items = sorted(items, key=lambda i: (i['name'].casefold(), i['id']))
    cats = sorted({(i['category_id'], i['category_name']) for i in items}, key=lambda pair: pair[1])
    return {'items': items, 'categories': [node(cid, name, [i for i in items if i['category_id'] == cid]) for cid, name in cats],
            'overall': node('overall', 'Overall', items)}


def refresh(period, source=None, as_of=None):
    if period not in PERIODS:
        raise ValueError('Choose daily, weekly or monthly.')
    source = source or load_source()
    if source['latest_day'] is None:
        raise ValueError('No eligible recorded sales are available.')
    as_of = as_of or timezone.localdate()
    revision = run_revision(source, period, as_of)
    run = HierarchyForecastRun.objects.filter(engine_version=VERSION, period=period, source_revision=revision).first()
    if run:
        if digest(run.findings) != run.checksum:
            raise ValueError('Saved hierarchy checksum failed.')
        return run, False
    data = build(source, period, as_of)
    with transaction.atomic():
        if load_source()['revision'] != source['revision']:
            raise ValueError('Sales or catalog changed during fitting; refresh again.')
        return HierarchyForecastRun.objects.get_or_create(engine_version=VERSION, period=period,
            source_revision=revision, defaults={'cutoff': data['cutoff'], 'findings': data, 'checksum': digest(data)})


def report(period):
    source = load_source()
    revision = run_revision(source, period, timezone.localdate()) if source['latest_day'] else None
    run = HierarchyForecastRun.objects.filter(engine_version=VERSION, period=period, source_revision=revision).first()
    if run is None or digest(run.findings) != run.checksum:
        return {'status': 'refresh_required', 'period': period, 'views': None,
                'message': 'Refresh forecasts to prepare the hierarchy for this sales history.', 'audit': source['audit']}
    return {**run.findings, 'status': 'ready', 'run_id': run.pk, 'generated_at': run.created_at.isoformat()}
