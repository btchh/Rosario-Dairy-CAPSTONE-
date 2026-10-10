"""Daily calendar SARIMA, selected against the actual reporting horizon.

The daily clock supplies observable weekday seasonality even when monthly
history is too short for an annual seasonal model. No target-period tickets
or future customer labels are inputs to a prediction.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX
from threadpoolctl import threadpool_limits

from .contract import VERSION, LIMITATIONS, Options, period_acceptance
from .rebuilt import aggregate, baselines, measured, period_end, row, split_periods


@dataclass(frozen=True)
class DailyModel:
    window: int
    transform: str
    order: tuple = (1, 0, 0)
    seasonal_order: tuple = (1, 0, 0, 7)

    def values(self):
        return {'name': f'daily_sarima_{self.order}_{self.seasonal_order}_{self.transform}_{self.window}',
                **asdict(self)}


def specifications():
    return tuple(DailyModel(window, transform, order, seasonal)
                 for window in (91, 365) for transform in ('level', 'log_mean')
                 for order, seasonal in (((1, 0, 0), (1, 0, 0, 7)),
                                         ((0, 1, 1), (0, 0, 1, 7))))


def fit_path(history, spec, steps):
    """Return expected daily currency, including lognormal mean correction."""
    training = history.iloc[-spec.window:].astype(float)
    if steps < 1 or len(training) < 28 or not np.isfinite(training).all() or (training < 0).any():
        raise ValueError('Daily SARIMA needs at least 28 finite nonnegative observations')
    if training.std() < 1e-8:
        return np.full(steps, float(training.iloc[-1]))
    scale = max(float(training.mean()), 1.)
    encoded = training / scale
    if spec.transform == 'log_mean':
        encoded = np.log1p(encoded)
    elif spec.transform != 'level':
        raise ValueError('Unknown daily revenue transformation')
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter('ignore')
        fitted = SARIMAX(encoded, order=spec.order, seasonal_order=spec.seasonal_order,
                         trend='n' if spec.order[1] else 'c',
                         enforce_stationarity=True, enforce_invertibility=True).fit(
                             disp=False, maxiter=100, cov_type='none')
        if not fitted.mle_retvals.get('converged', False):
            fitted = fitted.model.fit(start_params=fitted.params, method='powell',
                                      disp=False, maxiter=60, maxfun=1500, cov_type='none')
        if not fitted.mle_retvals.get('converged', False):
            raise ValueError('Daily SARIMA optimizer did not converge')
        prediction = fitted.get_forecast(steps=steps)
        point = np.asarray(prediction.predicted_mean, dtype=float)
        if spec.transform == 'log_mean':
            # Forecast the monetary expectation rather than summing medians.
            point = np.expm1(point + .5 * np.asarray(prediction.var_pred_mean))
    point = np.maximum(0., point * scale)
    if not np.isfinite(point).all() or (point >= 1e18).any():
        raise ValueError('Daily SARIMA produced invalid currency')
    return point


def daily_history(daily, options):
    # Reuse the source coverage checks, not the previous forecasting engine.
    aggregate(daily, 'monthly', options)
    return daily.sort_index().reindex(pd.date_range(options.training_start, daily.index.max()), fill_value=0.)


def walk(history, targets, spec, period, fixed_cutoff=None):
    rows = []
    fixed = None
    if fixed_cutoff is not None:
        prefix = history.loc[history.index < fixed_cutoff]
        try:
            fixed = pd.Series(fit_path(prefix, spec, (period_end(targets.index[-1], period) - prefix.index[-1]).days),
                              index=pd.date_range(prefix.index[-1] + pd.Timedelta(days=1), period_end(targets.index[-1], period)))
        except (ValueError, np.linalg.LinAlgError, OverflowError) as exc:
            return [row(stamp, actual, prefix.index[-1], period, reason=str(exc)) for stamp, actual in targets.items()]
    for stamp, actual in targets.items():
        prefix = history.loc[history.index < (fixed_cutoff if fixed_cutoff is not None else stamp)]
        try:
            end = period_end(stamp, period)
            point = float(fixed.loc[stamp:end].sum()) if fixed is not None else float(fit_path(prefix, spec, (end - stamp).days + 1).sum())
            item = row(stamp, actual, prefix.index[-1], period, [point, point, point])
            # Native daily marginal bounds do not describe a sum of correlated
            # days. Reporting ranges are calibrated on period validation errors.
            item['lower'] = item['upper'] = None
            rows.append(item)
        except (ValueError, np.linalg.LinAlgError, OverflowError) as exc:
            rows.append(row(stamp, actual, prefix.index[-1], period, reason=str(exc)))
    return rows


def select(history, validation, period, label, progress):
    attempts, ranked = [], []
    for spec in specifications():
        rows = walk(history, validation, spec, period)
        metric = measured(rows)
        attempts.append({'specification': spec.values(), 'metrics': metric,
                         'status': 'unavailable' if metric['failed_rows'] else 'evaluated'})
        if not metric['failed_rows']:
            ranked.append((metric['mae_pesos'], spec.values()['name'], spec, rows, metric))
    if not ranked:
        raise ValueError(f'No daily SARIMA candidate completed {period}/{label} validation')
    _, _, spec, rows, metric = min(ranked, key=lambda entry: entry[:2])
    if progress:
        progress(f'{period}/{label}: selected {spec.values()["name"]}, validation WAPE {metric["wape_percent"]}')
    return spec, rows, {'model': spec.values(), 'metrics': metric, 'attempts': attempts,
                        'diagnostics': {'calendar_season_days': 7, 'selection_horizon': period,
                                        'training_observations': int((history.index < validation.index[0]).sum())}}


def combine(parts, targets, period):
    rows = []
    for index, (stamp, actual) in enumerate(targets.items()):
        items = [value[index] for value in parts.values()]
        if any(item['date'] != str(stamp.date()) for item in items):
            raise ValueError('Component dates do not align')
        available = all(item['predicted'] is not None for item in items)
        point = sum(item['predicted'] for item in items) if available else None
        item = row(stamp, actual, pd.Timestamp(items[0]['trained_through']), period,
                   [point, point, point] if available else None,
                   None if available else 'A component fit was unavailable')
        item['lower'] = item['upper'] = None
        rows.append(item)
    return rows


def range_radius(rows):
    errors = [abs(item['actual'] - item['predicted']) for item in rows if item['predicted'] is not None]
    if len(errors) < 5:
        raise ValueError('Too few validation errors to estimate a reporting range')
    return float(np.quantile(errors, .8, method='higher'))


def add_ranges(rows, radius):
    return [{**item, 'lower': round(max(0., item['predicted'] - radius), 2),
             'upper': round(item['predicted'] + radius, 2)} if item['predicted'] is not None else item
            for item in rows]


def evaluate_period(daily, period, progress=None, options=None, components=None):
    options = options or Options()
    values = aggregate(daily, period, options)
    validation, test = split_periods(values, period, options)
    histories = {'total': daily_history(daily, options)}
    actuals = {'total': values}
    for name, component in (components or {}).items():
        histories[name] = component.reindex(histories['total'].index, fill_value=0.)
        actuals[name] = aggregate(histories[name], period, options)
    if components and not np.allclose(sum(histories[name] for name in components), histories['total'], atol=.005, rtol=0):
        raise ValueError('Component sales do not reconcile to total sales')
    models, validated, selections = {}, {}, {}
    for name, history in histories.items():
        models[name], validated[name], selections[name] = select(history, actuals[name].loc[validation.index], period, name, progress)
    component_validation = combine({name: validated[name] for name in components}, validation, period) if components else None
    strategy = 'components' if component_validation and measured(component_validation)['mae_pesos'] < measured(validated['total'])['mae_pesos'] else 'direct'
    chosen_validation = component_validation if strategy == 'components' else validated['total']
    plan = {name: models[name] for name in components} if strategy == 'components' else {'total': models['total']}
    # Freeze model, reconciliation choice and range BEFORE any test scoring.
    radius = range_radius(chosen_validation)
    selection = {'strategy': strategy, 'models': selections,
                 'validation_metrics': measured(add_ranges(chosen_validation, radius)),
                 'validation_dates': [str(stamp.date()) for stamp in validation.index],
                 'range': {'kind': 'validation_absolute_error_80_percent', 'radius_pesos': radius,
                           'sample_count': len(chosen_validation)}}
    signature = sha256(json.dumps(selection, sort_keys=True, allow_nan=False).encode()).hexdigest()
    if progress:
        progress(f'{period}: froze {strategy} selection {signature[:12]}; scoring 2025')
    tested = {name: walk(history, actuals[name].loc[test.index], models[name], period) for name, history in histories.items()}
    component_rows = combine({name: tested[name] for name in components}, test, period) if components else None
    rows = add_ranges(component_rows if strategy == 'components' else tested['total'], radius)
    fixed = []
    if period == 'monthly':
        fixed_parts = {name: walk(histories[name], test, spec, period, pd.Timestamp(options.test_start)) for name, spec in plan.items()}
        fixed = next(iter(fixed_parts.values())) if strategy == 'direct' else combine(fixed_parts, test, period)
        # A one-period validation range cannot be reused for a year-long path.
    last = histories['total'].index[-1]
    start = last + pd.Timedelta(days=1)
    next_period = {'status': 'incomplete_current_period', 'trained_through': str(last.date())}
    aligned = start.day == 1 if period == 'monthly' else start.weekday() == 0
    method = 'daily_sarima_' + strategy
    if aligned:
        end = period_end(start, period)
        try:
            point = sum(float(fit_path(histories[name], spec, (end - start).days + 1).sum()) for name, spec in plan.items())
            next_period = {'status': 'ready', 'date': str(start.date()), 'end_date': str(end.date()),
                           'trained_through': str(last.date()), 'days': (end - start).days + 1, 'method': method,
                           'combined': {'predicted_revenue': round(point, 2), 'lower_bound': round(max(0., point-radius), 2),
                                        'upper_bound': round(point+radius, 2), 'range_kind': selection['range']['kind']}}
        except (ValueError, np.linalg.LinAlgError, OverflowError) as exc:
            next_period.update(status='model_unavailable', reason=str(exc))
    metric = measured(rows)
    return {'method': method, 'strategy': strategy, 'selection': selection, 'selection_signature': signature,
            'validation_metrics': measured(add_ranges(chosen_validation, radius)),
            'validation_start': str(validation.index[0].date()), 'validation_end': str(period_end(validation.index[-1], period).date()),
            'rows': rows, 'metrics': {'combined': metric}, 'status': period_acceptance(metric),
            'fixed_origin_rows': fixed, 'fixed_origin_metrics': measured(fixed),
            'component_metrics': {name: measured(tested[name]) for name in components or {}},
            'component_comparison': {name: tested[name] for name in components or {}},
            'strategy_comparison': {'direct': measured(tested['total']), 'components': measured(component_rows) if component_rows else None},
            'baselines': baselines(values, test, period, rows), 'next_period': next_period}


def evaluate(daily, options=None, progress=None, components=None):
    options = options or Options()
    if daily.empty or daily.index.max() < pd.Timestamp(options.test_end):
        raise ValueError('The configured test coverage is not yet available')
    periods = {period: evaluate_period(daily, period, progress, options, components) for period in ('monthly', 'weekly')}
    monthly = periods['monthly']['fixed_origin_rows']
    complete = len(monthly) == 12 and all(item['predicted'] is not None for item in monthly)
    annual = {'date': options.test_start, 'end_date': options.test_end, 'trained_through': monthly[0]['trained_through'],
              'actual': round(sum(item['actual'] for item in monthly), 2),
              'predicted': round(sum(item['predicted'] for item in monthly), 2) if complete else None,
              'lower': None, 'upper': None, 'evaluation_kind': 'fixed_origin_daily_path'}
    annual_metric = measured([annual])
    periods['yearly'] = {'method': periods['monthly']['method'] + '_annual_sum', 'rows': [annual],
                         'metrics': {'combined': annual_metric}, 'status': period_acceptance(annual_metric),
                         'validation_metrics': None, 'baselines': {}, 'next_period': {'status': 'insufficient_evaluation'}}
    breakdown = {str(year): {'total': round(float(daily[daily.index.year == year].sum()), 2),
                             **{name: round(float(value[value.index.year == year].sum()), 2) for name, value in (components or {}).items()}}
                 for year in sorted(set(daily.index.year)) if year >= pd.Timestamp(options.training_start).year}
    return {'version': VERSION, 'selected_setup': 'sarima', 'selection_status': 'selected',
            'selection_trained_through': str((pd.Timestamp(options.test_start)-pd.Timedelta(days=1)).date()),
            'configuration': options.values(), 'data_start': str(daily.index.min().date()), 'data_end': str(daily.index.max().date()),
            'data_provenance': {'source': 'non-voided transactions', 'model_training_start': options.training_start,
                                'model_frequency': 'daily', 'calendar_season_days': 7,
                                'component_rule': 'Sale-time customer label contains feedin; all remaining revenue stays in other_sales',
                                'yearly_revenue_breakdown': breakdown, 'unrecorded_days': 'zero recorded revenue within workbook coverage',
                                'evaluation_kind': 'retrospective; 2025 was previously inspected'},
            'setups': {'sarima': {'setup': 'sarima', 'periods': periods}}, 'limitations': LIMITATIONS}
