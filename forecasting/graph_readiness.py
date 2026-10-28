"""Describe saved projections without treating chart availability as approval.

This module never fits, queries, changes predictions, or invents model evidence.
Issued values retain their provenance instead of borrowing a reconstructed fit.
"""
from datetime import date


def _metrics(value):
    if not isinstance(value, dict):
        return None
    return {'wape_percent': value.get('wape', value.get('wape_percent')),
            'periods': value.get('sample_count', value.get('periods', 0)),
            'basis': value.get('basis')}


def _component(item, artifacts, unit, cutoff):
    component = artifacts.get(item['signature'], {})
    state = component.get('state') or {}
    specification = state.get('specification') or {}
    seasonal_order = specification.get('seasonal_order')
    seasonal = bool(seasonal_order and any(seasonal_order[:3]))
    values = state.get('values') or []
    name = state.get('name')
    validation = component.get('validation') or {}
    return {
        'name': name,
        'family': ('SARIMA' if seasonal else 'ARIMA') if name else None,
        'order': specification.get('order'),
        'seasonal_order': seasonal_order,
        'seasonal_terms_enabled': seasonal,
        'forecast_origin': cutoff.isoformat(),
        'training_start': state.get('training_start'),
        'trained_through': state.get('trained_through'),
        'training_period': unit,
        'observed_periods': sum(value is not None for value in values),
        'missing_periods': sum(value is None for value in values),
        'selection_basis': ('primary_monthly_sarima' if item.get('primary') else
                            component.get('selection', 'earlier_validation_only' if validation else None)),
        'candidate_models': list(validation),
        'validation': _metrics(validation.get(name)),
        'test': _metrics(component.get('test')),
        'evaluation_period': unit,
        'production_approved': False,
    }


def describe_graph(data, ctx, artifacts):
    """Add diagnostics to a response copy; immutable saved findings stay intact."""
    current = next((point['date'] for view in data['views'].values()
                    for point in view['points'] if point['is_current']), None)
    views = {}
    overall_available = overall_expected = 0
    for view, result in data['views'].items():
        points = []
        for point in result['points']:
            available = point['past_prediction'] is not None or point['future_prediction'] is not None
            # An original issued value may use another model/run. Never attach
            # the newly reconstructed model's diagnostics to that issued value.
            details = None
            if available and point['prediction_basis'] != 'issued_forecast':
                target = date.fromisoformat(point['date'])
                components = {}
                for segment in ('regular', 'feeding') if view == 'combined' else (view,):
                    item = ctx['inputs'][(target, segment)]
                    cutoff = item['cutoff']
                    components[segment] = _component(item, artifacts, ctx['unit'], cutoff)
                details = ({'name': 'sum_of_component_predictions', 'components': components,
                            'production_approved': False} if view == 'combined' else components[view])
            points.append({**point, 'model_details': details})
        available = sum(p['past_prediction'] is not None or p['future_prediction'] is not None for p in points)
        expected = len(points)
        status = 'available' if available == expected and expected else 'partially_available' if available else 'unavailable'
        current_details = next((p['model_details'] for p in points if p['date'] == current), None)
        views[view] = {**result, 'points': points, 'model_details': current_details,
                       'readiness': {'status': status, 'available_prediction_points': available,
                                     'expected_prediction_points': expected, 'production_approved': False}}
        overall_available += available
        overall_expected += expected
    status = ('available' if overall_available == overall_expected and overall_expected else
              'partially_available' if overall_available else 'unavailable')
    return {**data, 'views': views,
            'readiness': {'status': status, 'production_approved': False,
                          'available_prediction_points': overall_available,
                          'expected_prediction_points': overall_expected,
                          'publication_status': 'research_only',
                          'primary_monthly_status_url': '/api/forecasting/models/',
                          'limitations': [
                              'Graph status describes saved chart availability, not validated production approval.',
                              'Model selection may choose a nonseasonal ARIMA when earlier history does not support seasonal terms.',
                              'Component validation/test metrics do not establish yearly or Combined forecast accuracy.',
                              'Historical tests are reconstructed; original issued forecasts retain their own basis.',
                          ]}}
