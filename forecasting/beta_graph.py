"""Centered, three-series beta forecasts. Fitting occurs only in offline refresh.

Legacy demo scope freezes a pre-2026 training source. Ordinary local records
remain included in real-scope history.
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
from .data import load_snapshots
from .learning import ENGINE_VERSION, MIN_MONTHS, shift_month
from .lifecycle import _commit_lock, valid_coverage
from .models import GraphForecastRun, GraphPrediction, MonthlyModelRun
from .provisional import digest, series, train, fit, future_rows, project
from .seasonal import is_sarima_artifact
from .chart import period_start, period_end
from .graph_readiness import describe_graph
from .graph_sarima import regular_weekly_dependency, regular_weekly_component

VERSION = 'centered-beta-sarima-v6'
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
                    'training_start': rows[0]['date'], 'trained_through': artifact['trained_through'],
                    'primary_model_id': getattr(run, 'pk', None),
                    'primary_monthly_backtest': {'basis': 'rolling_one_month_ahead',
                        'metrics': run.findings.get('selected_test'),
                        'baseline_metrics': run.findings.get('baseline_test'),
                        'selected_baseline': run.findings.get('selected_baseline')}}
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
    return None


def _regular_monthly_series(snapshot):
    """Use reconciled zero months without treating unknown invoices as zero."""
    rows = series(snapshot, 'regular', 'monthly')
    zero_months = [month for month, coverage in valid_coverage(snapshot).items()
                   if coverage.confirmed_zero]
    if not zero_months:
        return rows
    actuals = {date.fromisoformat(row['date']): row['actual'] for row in rows}
    for month in zero_months:
        if actuals.get(month) is None:
            actuals[month] = 0.0
    rows = []
    month = min(actuals)
    last_month = max(actuals)
    while month <= last_month:
        rows.append({'date': month.isoformat(), 'actual': actuals.get(month)})
        month = shift_month(month)
    return rows


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
    cutoffs = {}
    for target in calendar:
        cutoff = (target if target < current else
                  current if period != 'yearly' else as_of.replace(day=1))
        cutoffs[target] = min(cutoff, period_start(DEMO_ORIGIN, unit)) if scope == 'demo2026' else cutoff
    displayed_cutoff = as_of + timedelta(days=1)
    loaded = load_snapshots([*cutoffs.values(), *([displayed_cutoff] if scope == 'real' else [])])
    snapshots = {cutoff: loaded[cutoff] for cutoff in cutoffs.values()}
    regular_monthly_rows = {}
    weekly_dependencies = {}
    inputs = {}
    for target in calendar:
        # Completed targets retain a pre-target reconstruction. The current
        # period and every later target share one latest closed-period origin.
        cutoff = cutoffs[target]
        snap = snapshots[cutoff]
        for segment in ('regular', 'feeding'):
            weekly = None
            if segment == 'regular' and unit == 'monthly':
                if cutoff not in regular_monthly_rows:
                    regular_monthly_rows[cutoff] = _regular_monthly_series(snap)
                rows = regular_monthly_rows[cutoff]
            elif segment == 'regular' and unit == 'weekly':
                if cutoff not in weekly_dependencies:
                    weekly_dependencies[cutoff] = regular_weekly_dependency(snap)
                weekly = weekly_dependencies[cutoff]
                # Keep the provisional source for short histories; eligible
                # annual SARIMA uses only complete source-boundary weeks.
                rows = (series(snap, segment, unit) if weekly['status'] == 'insufficient_history'
                        else weekly['rows'])
            else:
                rows = series(snap, segment, unit)
            primary = _primary(snap, rows) if segment == 'regular' and unit == 'monthly' else None
            last_gap = max((i for i, row in enumerate(rows) if row['actual'] is None), default=-1)
            primary_required = segment == 'regular' and unit == 'monthly' and len(rows)-last_gap-1 >= MIN_MONTHS
            signature = digest({'version': VERSION, 'unit': unit, 'segment': segment, 'rows': rows,
                                'primary': primary, 'weekly': weekly})
            inputs[(target, segment)] = {'rows': rows, 'primary': primary, 'signature': signature,
                                         'weekly': weekly, 'primary_required': primary_required,
                                         'cutoff': cutoff, 'source_revision': snap.revision}
    if scope == 'real':
        displayed = loaded[displayed_cutoff]
        sales = [(s.id, s.day, s.segment, s.revenue) for s in displayed.sales]
    else:
        sales = []
        for row in Transaction.objects.filter(source_reference__regex=r'^2026:[0-9]{6}$', is_voided=False).order_by('pk').values(
                'pk', 'created_at', 'source_sales_segment', 'total_amount'):
            day = timezone.localtime(row['created_at']).date()
            if day.year == 2026 and day <= as_of:
                sales.append((row['pk'], day, row['source_sales_segment'], row['total_amount']))
    identity = {'version': VERSION, 'period': period, 'scope': scope, 'as_of': as_of.isoformat(),
                'training_sources': {day.isoformat(): snap.revision for day, snap in snapshots.items()},
                'inputs': {f'{target}:{segment}': item['signature'] for (target, segment), item in inputs.items()}}
    if scope == 'demo2026':
        identity['display'] = digest([(pk, day.isoformat(), segment, str(value)) for pk, day, segment, value in sales])
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


def annual_outlook(component, target, cutoff, rows, current_actual=None):
    """Combine completed actual months and the remaining monthly model path.

    This is a current-year estimate, not an annual forecast issued in January.
    Unknown closed months invalidate the sum. The current month's recorded
    revenue is a disclosed lower bound, never a prorated run-rate estimate.
    Filtering saved parameters here does not optimize or alter the model.
    """
    months = [target.replace(month=month) for month in range(1, cutoff.month)]
    actuals, invalid = {}, []
    for row in rows:
        month = date.fromisoformat(row['date'])
        if month not in months:
            continue
        value = row['actual']
        if value is not None:
            try:
                value = float(value)
                if not math.isfinite(value) or not 0 <= value < 1e18:
                    raise ValueError('Invalid actual revenue.')
            except (ValueError, TypeError, OverflowError):
                invalid.append(month.isoformat())
                value = None
        actuals[month] = value
    missing = [month.isoformat() for month in months if actuals.get(month) is None]
    observed = round(sum(actuals[month] for month in months if actuals.get(month) is not None), 2)
    invalid_current = False
    if current_actual is not None:
        try:
            current_actual = float(current_actual)
            if not math.isfinite(current_actual) or not 0 <= current_actual < 1e18:
                raise ValueError('Invalid current revenue.')
        except (ValueError, TypeError, OverflowError):
            current_actual, invalid_current = None, True
    details = {'completed_months': len(months), 'missing_completed_months': missing,
               'invalid_completed_months': invalid, 'invalid_current_month_actual': invalid_current,
               'observed_revenue': observed, 'forecast_months': 13-cutoff.month,
               'forecast_revenue': None, 'current_month': cutoff.isoformat(),
               'current_month_actual': current_actual, 'current_month_model_prediction': None,
               'current_month_estimate': None, 'current_month_floor_applied': False,
               'prediction': None}
    if missing or invalid_current or not component.get('state'):
        return details
    targets = [target.replace(month=month).isoformat() for month in range(cutoff.month, 13)]
    try:
        points = future_rows(component, targets, 'monthly')
        if [point['date'] for point in points] != targets:
            return details
        values = [float(point['predicted']) for point in points]
        if not all(math.isfinite(value) and 0 <= value < 1e18 for value in values):
            return details
        model_value = values[0]
        if current_actual is not None:
            values[0] = max(model_value, current_actual)
        remaining = round(sum(values), 2)
        result = round(observed+remaining, 2)
        if not math.isfinite(result) or not 0 <= result < 1e18:
            return details
        return {**details, 'forecast_revenue': remaining,
                'current_month_model_prediction': model_value, 'current_month_estimate': values[0],
                'current_month_floor_applied': values[0] > model_value, 'prediction': result}
    except (ValueError, TypeError, KeyError, ArithmeticError, np.linalg.LinAlgError):
        return details


def _horizon(target, cutoff, period):
    """Calendar steps from an exclusive cutoff, including the current bucket."""
    if period == 'weekly':
        return (target-cutoff).days//7+1
    if period == 'monthly':
        return (target.year-cutoff.year)*12+target.month-cutoff.month+1
    # An annual hybrid is the current year (offset zero). Later annual paths
    # are whole future years; no claim is made that October is a year boundary.
    return target.year-cutoff.year


def _issue_details(record, target, period):
    run_as_of = record['run__as_of']
    cutoff = period_start(run_as_of, 'monthly' if period == 'yearly' else period)
    return {'prediction_id': record['pk'], 'run_id': record['run_id'],
            'engine_version': record['run__engine_version'], 'issued_at': record['issued_at'].isoformat(),
            'run_as_of': run_as_of.isoformat(), 'forecast_origin': cutoff.isoformat(),
            'cutoff_basis': 'issuing_run_calendar',
            'source_revision': record['run__source_revision'], 'revision_kind': 'graph_input_revision',
            'horizon': _horizon(target, cutoff, period),
            'horizon_unit': 'year_offset' if period == 'yearly' else period}


def _issued_predictions(period, target, as_of):
    target_time = timezone.make_aware(datetime.combine(target, time.min))
    extraction_end = timezone.make_aware(datetime.combine(as_of+timedelta(days=1), time.min))
    records = list(GraphPrediction.objects.filter(run__scope='real', run__period=period,
        target=target, issued_at__lt=min(target_time, extraction_end)).values(
            'pk', 'run_id', 'view', 'value', 'issued_at', 'run__as_of',
            'run__engine_version', 'run__source_revision'))
    bundles = defaultdict(dict)
    for record in records:
        bundles[record['run_id']][record['view']] = record
    # Prefer the most recent complete issue across all views. Picking the newest
    # component independently can mix refreshes and break Combined = R + F.
    for values in bundles.values():
        if (all(view in values for view in VIEWS)
                and abs(values['combined']['value']-values['regular']['value']-values['feeding']['value']) <= Decimal('0.01')):
            return {view: {'value': float(values[view]['value']),
                           'provenance': _issue_details(values[view], target, period)} for view in VIEWS}
    values = {}
    for record in records:
        if record['view'] in ('regular', 'feeding'):
            values.setdefault(record['view'], {'value': float(record['value']),
                                               'provenance': _issue_details(record, target, period)})
    if values:
        # These component issues remain useful, but no coherent Combined issue
        # exists. Do not relabel a newly computed sum as an original forecast.
        values['combined'] = None
    return values


def build(ctx, artifacts=None, *, allow_training=True, saved_views=None):
    artifacts = dict(artifacts or {})
    period, scope, as_of = ctx['period'], ctx['scope'], ctx['as_of']
    current = period_start(as_of, period)
    totals = defaultdict(dict)
    monthly_totals = defaultdict(dict)
    for _, day, segment, amount in ctx['sales']:
        if segment in ('regular', 'feeding'):
            bucket = period_start(day, period)
            totals[bucket][segment] = totals[bucket].get(segment, Decimal('0'))+amount
            month = day.replace(day=1)
            monthly_totals[month][segment] = monthly_totals[month].get(segment, Decimal('0'))+amount
    views = {}
    predictions = {}
    annual_details = {}
    issued = {target: _issued_predictions(period, target, as_of) for target in ctx['calendar']
              if scope == 'real'}
    for target in ctx['calendar']:
        is_annual_outlook = scope == 'real' and period == 'yearly' and target == current
        if saved_views is not None:
            for view in VIEWS:
                point = next(p for p in saved_views[view]['points'] if p['date'] == target.isoformat())
                predictions[(target, view)] = point['past_prediction'] if target < current else point['future_prediction']
            if not is_annual_outlook:
                continue
        for segment in ('regular', 'feeding'):
            item = ctx['inputs'][(target, segment)]
            signature = item['signature']
            if signature not in artifacts:
                if not allow_training:
                    artifacts[signature] = {'status': 'unavailable'}
                elif item.get('weekly') and item['weekly']['status'] != 'insufficient_history':
                    artifacts[signature] = regular_weekly_component(item['weekly'])
                elif item['primary']:
                    artifacts[signature] = {'state': item['primary'], 'status': 'provisional',
                        'selection': 'reuse_primary_monthly_sarima_selection',
                        'model_basis': 'primary_monthly_sarima',
                        'primary_model_id': item['primary'].get('primary_model_id'), 'test': None,
                        'primary_monthly_backtest': item['primary'].get('primary_monthly_backtest')}
                elif item.get('primary_required'):
                    artifacts[signature] = {'status': 'primary_refresh_required',
                                            'model_basis': 'primary_monthly_sarima'}
                else:
                    artifacts[signature] = _train(item['rows'], ctx['unit'])
                    if item.get('weekly'):
                        artifacts[signature] = {**artifacts[signature],
                            'model_basis': 'provisional_fallback',
                            'fallback_reason': 'annual_weekly_sarima_requires_130_contiguous_completed_weeks'}
            if is_annual_outlook:
                recorded = monthly_totals.get(item['cutoff'], {}).get(segment)
                details = annual_outlook(artifacts[signature], target, item['cutoff'], item['rows'],
                                         float(recorded) if recorded is not None else None)
                annual_details[segment] = details
                predictions[(target, segment)] = details['prediction']
            else:
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
            issue = issued.get(target, {}).get(view)
            if scope == 'real' and target < current and view in issued.get(target, {}):
                predicted = issue['value'] if issue is not None else None
                if predicted is not None:
                    basis = 'issued_forecast'
            if target < current and not partial and actual is not None and predicted is not None:
                evaluations[basis].append({'actual': float(actual), 'predicted': predicted})
            item = ctx['inputs'][(target, 'regular' if view == 'combined' else view)]
            annual = scope == 'real' and period == 'yearly' and target == current
            outlook_basis = ('completed_month_actuals_plus_remaining_monthly_forecast' if annual else
                             'single_origin_monthly_forecast_sum' if period == 'yearly' else
                             'latest_closed_period_forecast') if target >= current and scope == 'real' else None
            details = ({'components': {segment: annual_details.get(segment) for segment in ('regular', 'feeding')}}
                       if annual and view == 'combined' else annual_details.get(view) if annual else None)
            points.append({'date': target.isoformat(), 'end_date': period_end(target, period).isoformat(),
                           'actual': float(actual) if actual is not None else None,
                           'past_prediction': predicted if target < current else None,
                           'future_prediction': predicted if target >= current else None,
                           'prediction_basis': basis, 'is_current': target == current,
                           'is_partial_period': partial,
                           'issued_prediction': issue['value'] if issue is not None else None,
                           'issued_provenance': issue['provenance'] if issue is not None else None,
                           'forecast_origin': (issue['provenance']['forecast_origin']
                                               if basis == 'issued_forecast' else item['cutoff'].isoformat()),
                           'horizon': (issue['provenance']['horizon'] if basis == 'issued_forecast'
                                       else _horizon(target, item['cutoff'], period)),
                           'horizon_unit': 'year_offset' if period == 'yearly' else period,
                           'outlook_basis': outlook_basis, 'is_nowcast': annual,
                           'annual_outlook': details})
        views[view] = {'label': LABELS[view], 'points': points,
                       'evaluation': {basis: score(rows) for basis, rows in evaluations.items()},
                       'message': None if any(p['past_prediction'] is not None or p['future_prediction'] is not None
                                              for p in points) else 'Not enough sales history yet.'}
    return {'scope': scope, 'is_demo': scope == 'demo2026', 'training_scope': 'real', 'period': period,
            'as_of': as_of.isoformat(), 'available_periods': list(PERIODS), 'available_views': list(VIEWS),
            'range': {'start_date': ctx['calendar'][0].isoformat(), 'end_date': period_end(ctx['calendar'][-1], period).isoformat(),
                      'past_periods': 2 if period == 'yearly' else 3, 'future_periods': 2 if period == 'yearly' else 3},
            'views': views, 'artifacts': {} if saved_views is not None else
            {item['signature']: artifacts[item['signature']] for item in ctx['inputs'].values()}}


def build_seasonal_outlook(ctx, data, artifacts, *, saved_outlook=None, allow_projection=True):
    """A full seasonal display path from the same saved component origins.

    This adds no issued forecasts or accuracy claims. GETs replay saved values
    and update only the current recorded actual; they never fill a missing path.
    """
    period = ctx['period']
    if period == 'yearly':
        return None
    current = period_start(ctx['as_of'], period)
    count = 52 if period == 'weekly' else 12
    dates = [advance(current, period, n) for n in range(count)]
    targets = [target.isoformat() for target in dates]
    projected = {}
    if allow_projection and saved_outlook is None:
        for segment in ('regular', 'feeding'):
            item = ctx['inputs'][(current, segment)]
            component = artifacts.get(item['signature'], {})
            values = {}
            if component.get('state'):
                try:
                    rows = future_rows(component, targets, period)
                    if [row['date'] for row in rows] != targets:
                        raise ValueError('Incomplete seasonal forecast path.')
                    for row in rows:
                        value = float(row['predicted'])
                        if not math.isfinite(value) or not 0 <= value < 1e18:
                            raise ValueError('Invalid seasonal forecast value.')
                        values[row['date']] = value
                except (ValueError, KeyError, TypeError, ArithmeticError, np.linalg.LinAlgError):
                    values = {}
            projected[segment] = values
    saved_outlook = saved_outlook if isinstance(saved_outlook, dict) else {}
    saved_views = saved_outlook.get('views')
    retained = {}
    for view in VIEWS:
        saved_view = saved_views.get(view) if isinstance(saved_views, dict) else None
        points = saved_view.get('points') if isinstance(saved_view, dict) else None
        centered = {point['date']: point for point in data['views'][view]['points']}
        cutoff = ctx['inputs'][(current, 'regular' if view == 'combined' else view)]['cutoff']
        valid = (saved_outlook.get('period') == period
                 and saved_outlook.get('as_of') == ctx['as_of'].isoformat()
                 and isinstance(points, list) and len(points) == count)
        if valid:
            for n, (target, point) in enumerate(zip(dates, points)):
                if not isinstance(point, dict):
                    valid = False
                    break
                value = point.get('future_prediction')
                valid = ('future_prediction' in point and point.get('date') == target.isoformat()
                    and point.get('end_date') == period_end(target, period).isoformat()
                    and 'past_prediction' in point and point['past_prediction'] is None
                    and point.get('is_current') is (n == 0) and point.get('is_partial_period') is (n == 0)
                    and point.get('forecast_origin') == cutoff.isoformat()
                    and type(point.get('horizon')) is int
                    and point['horizon'] == _horizon(target, cutoff, period)
                    and point.get('horizon_unit') == period
                    and point.get('prediction_basis') == ('fixed_origin_demo' if ctx['scope'] == 'demo2026' else 'forecast')
                    and (value is None or (type(value) in (int, float) and math.isfinite(value) and 0 <= value < 1e18))
                    and (target.isoformat() not in centered or
                         value == centered[target.isoformat()]['future_prediction']))
                if not valid:
                    break
        retained[view] = points if valid else None
    # A valid checksum does not make an inconsistent component sum a forecast.
    if all(retained.values()):
        for regular, feeding, combined in zip(retained['regular'], retained['feeding'], retained['combined']):
            parts = [regular['future_prediction'], feeding['future_prediction']]
            expected = round(sum(parts), 2) if all(value is not None for value in parts) else None
            if combined['future_prediction'] != expected:
                retained['combined'] = None
                break
    else:
        retained['combined'] = None
    views = {}
    for view in VIEWS:
        centered = {point['date']: point for point in data['views'][view]['points']}
        saved_points = retained[view]
        points = []
        for n, target in enumerate(dates):
            day = target.isoformat()
            item = ctx['inputs'][(current, 'regular' if view == 'combined' else view)]
            value = None
            if saved_points is not None:
                point = dict(saved_points[n])
            elif allow_projection and day in centered:
                point = dict(centered[day])
            else:
                if allow_projection:
                    parts = [projected.get(segment, {}).get(day) for segment in
                             (('regular', 'feeding') if view == 'combined' else (view,))]
                    if all(part is not None for part in parts):
                        value = round(sum(parts), 2)
                        if not math.isfinite(value) or not 0 <= value < 1e18:
                            value = None
                point = {'date': day, 'end_date': period_end(target, period).isoformat(),
                         'actual': None, 'past_prediction': None, 'future_prediction': value,
                         'prediction_basis': 'fixed_origin_demo' if ctx['scope'] == 'demo2026' else 'forecast',
                         'is_current': n == 0, 'is_partial_period': n == 0,
                         'issued_prediction': None, 'issued_provenance': None,
                         'forecast_origin': item['cutoff'].isoformat(),
                         'horizon': _horizon(target, item['cutoff'], period), 'horizon_unit': period,
                         'outlook_basis': 'latest_closed_period_forecast' if ctx['scope'] == 'real' else None,
                         'is_nowcast': False, 'annual_outlook': None}
            point['actual'] = centered.get(day, {}).get('actual') if n == 0 else None
            points.append(point)
        views[view] = {'label': LABELS[view], 'points': points, 'evaluation': {},
                       'message': None if any(p['future_prediction'] is not None for p in points)
                       else 'Refresh saved forecasts or provide sufficient observed history.'}
    return {'period': period, 'as_of': ctx['as_of'].isoformat(),
            'range': {'start_date': targets[0], 'end_date': period_end(dates[-1], period).isoformat(),
                      'periods': count},
            'basis': 'single_origin_component_forecasts', 'production_approved': False, 'views': views}


def _describe_outlook(outlook, ctx, artifacts):
    if outlook is None:
        return None
    current = period_start(ctx['as_of'], ctx['period'])
    inputs = {(date.fromisoformat(point['date']), segment): ctx['inputs'][(current, segment)]
              for point in outlook['views']['regular']['points'] for segment in ('regular', 'feeding')}
    return describe_graph(outlook, {**ctx, 'inputs': inputs}, artifacts)


def checked(findings):
    if not isinstance(findings, dict):
        return None
    data = dict(findings)
    checksum = data.pop('checksum', None)
    try:
        return data if checksum == digest(data) else None
    except (ValueError, TypeError, OverflowError):
        return None


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
    findings['seasonal_outlook'] = build_seasonal_outlook(ctx, findings, findings['artifacts'])
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
    saved = checked(run.findings) if run else None
    # Actuals and evaluations are live ledger values, while immutable issued
    # predictions and fitted states retain their original provenance. Never fit
    # missing states during a GET, including when saved findings are corrupt.
    data = build(ctx, saved.get('artifacts', {}) if saved else None,
                 allow_training=False, saved_views=saved['views'] if saved else None)
    data['seasonal_outlook'] = _describe_outlook(
        build_seasonal_outlook(ctx, data, saved.get('artifacts', {}) if saved else {},
                              saved_outlook=saved.get('seasonal_outlook') if saved else None,
                              allow_projection=False),
        ctx, saved.get('artifacts', {}) if saved else {})
    if saved is None:
        for view in data['views'].values():
            if view['message'] is not None:
                view['message'] = 'Refresh saved forecasts to make predictions available.'
    data = {key: value for key, value in data.items() if key != 'artifacts'}
    return describe_graph(
        {**data, 'status': 'ready' if saved is not None else 'refresh_required',
         'model_id': run.pk if run else None}, ctx, saved.get('artifacts', {}) if saved else {})
