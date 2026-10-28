"""Describe saved projections without treating chart availability as approval.

This module never fits, queries, changes predictions, or invents model evidence.
Issued values retain their provenance instead of borrowing a reconstructed fit.
"""
from datetime import date, timedelta
import math


def _metrics(value):
    if not isinstance(value, dict):
        return None
    return {'wape_percent': value.get('wape', value.get('wape_percent')),
            'periods': value.get('sample_count', value.get('periods', 0)),
            'basis': value.get('basis')}


def _day(value):
    try:
        return value if type(value) is date else date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _start(day, unit):
    return day-timedelta(days=day.weekday()) if unit == 'weekly' else day.replace(day=1)


def _steps(origin, target, unit):
    if origin is None or target is None:
        return None
    if unit == 'weekly':
        return (target-origin).days//7
    return (target.year-origin.year)*12+target.month-origin.month


def _previous(day, unit):
    if unit == 'weekly':
        return day-timedelta(days=7)
    index = day.year*12+day.month-2
    return date(index//12, index % 12+1, 1)


def _last_observed(item, state, unit):
    through = _day(state.get('trained_through'))
    values = state.get('values')
    if through is not None and isinstance(values, list):
        for value in reversed(values):
            if value is not None:
                return through
            through = _previous(through, unit)
        return None
    observed = [_day(row.get('date')) for row in item.get('rows', [])
                if row.get('actual', row.get('revenue')) is not None]
    observed = [day for day in observed if day is not None]
    return max(observed, default=through)


def _freshness(item, state, unit, cutoff):
    """Coverage age at the extraction origin, distinct from forecast horizon."""
    expected = _previous(_start(cutoff, unit), unit)
    observed = _last_observed(item, state, unit)
    missing = max(0, _steps(observed, expected, unit)) if observed is not None else None
    return {'status': ('unavailable' if observed is None else 'stale_data' if missing else 'current'),
            'latest_observed_period': observed.isoformat() if observed else None,
            'expected_latest_closed_period': expected.isoformat(),
            'missing_observation_periods': missing, 'period': unit,
            'coverage_verified': False,
            'basis': 'recorded_training_observations; invoice_presence_is_not_verified_coverage'}


def _aggregate_freshness(components):
    statuses = [value['status'] for value in components.values()]
    status = ('stale_data' if 'stale_data' in statuses else
              'current' if statuses and all(s == 'current' for s in statuses) else 'unavailable')
    return {'status': status, 'components': components, 'coverage_verified': False}


def _comparison(component, backtest):
    model = _metrics(backtest.get('metrics')) if backtest else None
    baseline = _metrics(backtest.get('baseline_metrics')) if backtest else None
    mw = model['wape_percent'] if model else None
    bw = baseline['wape_percent'] if baseline else None
    valid = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in (mw, bw))
    return {'selected_baseline': backtest.get('selected_baseline') if backtest else component.get('selected_baseline'),
            'model_wape_percent': mw, 'baseline_wape_percent': bw,
            'result': ('better' if mw < bw else 'worse' if mw > bw else 'equal') if valid else 'not_available',
            'basis': backtest.get('basis') if backtest else None}


def _component(item, artifacts, unit, cutoff, target, period, current):
    component = artifacts.get(item['signature'], {})
    state = component.get('state') or {}
    specification = state.get('specification') or {}
    seasonal_order = specification.get('seasonal_order')
    seasonal = bool(seasonal_order and any(seasonal_order[:3]))
    values = state.get('values') or []
    name = state.get('name')
    validation = component.get('validation') or {}
    flat_validation = 'wape' in validation or 'wape_percent' in validation
    family = ('SARIMA' if seasonal else 'ARIMA') if name else None
    primary = bool(item.get('primary'))
    primary_backtest = component.get('primary_weekly_backtest' if unit == 'weekly' else 'primary_monthly_backtest')
    freshness = _freshness(item, state, unit, cutoff)
    origin = _day(state.get('trained_through'))
    step = _steps(origin, target, unit)
    annual_start = cutoff if target == current and cutoff.year == target.year else target
    annual_end = target.replace(month=12, day=1)
    start_step = _steps(origin, annual_start, unit) if period == 'yearly' else None
    end_step = _steps(origin, annual_end, unit) if period == 'yearly' else None
    result = {
        'name': name, 'family': family,
        'display_label': ('Local level — nonseasonal ARIMA(0,1,0)' if name == 'local_level' else
                          'Nonseasonal ARIMA(1,0,0)' if name == 'ar1' else name),
        'order': specification.get('order'),
        'seasonal_order': seasonal_order,
        'seasonal_terms_enabled': seasonal,
        'forecast_origin': cutoff.isoformat(),
        'training_start': state.get('training_start'),
        'trained_through': state.get('trained_through'),
        'training_period': unit,
        'observed_periods': sum(value is not None for value in values),
        'missing_periods': sum(value is None for value in values),
        # Retain the existing point horizon separately: it is calendar-relative
        # to the extraction cutoff, whereas these steps start at fitted history.
        'projection_steps': step if period != 'yearly' and step is not None and step > 0 else None,
        'projection_step_start': start_step if start_step is not None and start_step > 0 else None,
        'projection_step_end': end_step if end_step is not None and end_step > 0 else None,
        'projection_step_unit': unit,
        'missing_observation_periods_before_current': freshness['missing_observation_periods'] if target >= current else None,
        'source_freshness': freshness,
        'selection_basis': component.get('selection') or
                           (f'primary_{unit}_sarima' if primary else 'earlier_validation_only' if validation else None),
        'model_basis': component.get('model_basis'),
        'primary_model_id': component.get('primary_model_id'),
        'candidate_models': component.get('candidate_models') or
                            ([name] if flat_validation and name else list(validation)),
        'validation': _metrics(validation if flat_validation else validation.get(name)),
        'validation_basis': component.get('validation_basis'),
        'test': None if primary else _metrics(component.get('test')),
        'primary_backtest': primary_backtest,
        'baseline_comparison': _comparison(component, primary_backtest),
        'evaluation_period': unit,
        'production_approved': False,
    }
    return result


def describe_graph(data, ctx, artifacts):
    """Add diagnostics to a response copy; immutable saved findings stay intact.

    The supplied points and inputs may describe the centered chart or a longer
    seasonal outlook. All metadata derives from the saved component states.
    """
    current = next((point['date'] for view in data['views'].values()
                    for point in view['points'] if point['is_current']), None)
    current_day = _day(current)
    views = {}
    overall_available = overall_expected = 0
    overall_freshness = {}
    for view, result in data['views'].items():
        points = []
        current_freshness = {}
        for point in result['points']:
            available = point['past_prediction'] is not None or point['future_prediction'] is not None
            # An original issued value may use another model/run. Never attach
            # the newly reconstructed model's diagnostics to that issued value.
            details = None
            target = date.fromisoformat(point['date'])
            components = {}
            if point['prediction_basis'] != 'issued_forecast':
                for segment in ('regular', 'feeding') if view == 'combined' else (view,):
                    item = ctx['inputs'][(target, segment)]
                    cutoff = item['cutoff']
                    components[segment] = _component(item, artifacts, ctx['unit'], cutoff, target,
                                                     data['period'], current_day or target)
                if available:
                    details = ({'name': 'sum_of_component_predictions', 'components': components,
                                'production_approved': False} if view == 'combined' else components[view])
                if point['is_current']:
                    current_freshness = {segment: value['source_freshness'] for segment, value in components.items()}
            points.append({**point, 'model_details': details})
        available = sum(p['past_prediction'] is not None or p['future_prediction'] is not None for p in points)
        expected = len(points)
        status = 'available' if available == expected and expected else 'partially_available' if available else 'unavailable'
        current_details = next((p['model_details'] for p in points if p['date'] == current), None)
        views[view] = {**result, 'points': points, 'model_details': current_details,
                       'readiness': {'status': status, 'available_prediction_points': available,
                                     'expected_prediction_points': expected, 'production_approved': False,
                                     'source_freshness': _aggregate_freshness(current_freshness)}}
        if view != 'combined':
            overall_freshness.update(current_freshness)
        overall_available += available
        overall_expected += expected
    status = ('available' if overall_available == overall_expected and overall_expected else
              'partially_available' if overall_available else 'unavailable')
    return {**data, 'views': views,
            'readiness': {'status': status, 'production_approved': False,
                          'available_prediction_points': overall_available,
                          'expected_prediction_points': overall_expected,
                          'source_freshness': _aggregate_freshness(overall_freshness),
                          'publication_status': 'research_only',
                          'primary_monthly_status_url': '/api/forecasting/models/',
                          'limitations': [
                              'Graph status describes saved chart availability, not validated production approval.',
                              'Seasonal regular-sales models and nonseasonal feeding fallbacks retain their actual model labels.',
                              'Projection steps start at the last fitted bucket; the legacy point horizon starts at the extraction cutoff.',
                              'Stale source observations cannot be repaired by refreshing or drawing seasonal curves.',
                              'Rolling one-period backtests do not establish accuracy at longer projection steps.',
                              'Component validation/test metrics do not establish yearly or Combined forecast accuracy.',
                              'Historical tests are reconstructed; original issued forecasts retain their own basis.',
                              'Current and future projections share one closed-period model origin; older issued values are separate comparisons.',
                              'The current annual outlook combines completed-month actuals with remaining monthly estimates and discloses any current-month actual floor.',
                          ]}}
