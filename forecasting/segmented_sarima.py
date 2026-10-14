"""Retrospective SARIMA-only comparison on a shared chronological protocol.

Both the existing daily grid and the expanded daily/calendar grid are selected
on Jan-Jun 2025, then frozen before scoring Jul-Dec. This is an experiment, not
the production publication contract or an independent untouched holdout.
"""
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from multiprocessing import get_context
import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX
from threadpoolctl import threadpool_limits

from .contract import Options, period_acceptance
from .daily_sarima import (
    DailyModel, add_ranges, combine, daily_history, fit_path as daily_fit,
    range_radius, specifications as existing_specifications, walk as daily_walk,
)
from .rebuilt import aggregate, baselines, before, measured, period_end, row, split_periods

VERSION = 'segmented-sarima-v1'
PROTOCOL = Options(validation_start='2025-01-01', test_start='2025-07-01')
LIMITATIONS = (
    '2025 was inspected before this experiment; these results are retrospective. '
    'Selection uses Jan-Jun 2025 only; scoring uses Jul-Dec 2025. '
    'The test has only six monthly periods. Milk-feeding has very few active dates; '
    'zero pre-program recorded revenue does not establish seasonal contract timing. '
    'Calendar SARIMA requires two complete annual cycles at every forecast origin. '
    'No synthetic revenue, future labels, future contracts, or test-fitted correction is used.'
)


@dataclass(frozen=True)
class Model:
    frequency: str
    window: int | None
    transform: str
    order: tuple
    seasonal_order: tuple

    def values(self):
        return {'name': json.dumps(asdict(self), sort_keys=True), **asdict(self)}


def specifications(period):
    original = tuple(Model('daily', item.window, item.transform, item.order, item.seasonal_order)
                     for item in existing_specifications())
    expanded = tuple(Model('daily', window, transform, order, seasonal)
                     for window in (56, 91, 182, 365)
                     for transform in ('level', 'log_mean')
                     for order, seasonal in (((1, 0, 0), (1, 0, 0, 7)),
                                             ((0, 1, 1), (0, 0, 1, 7))))
    lag = {'monthly': 12, 'weekly': 52}[period]
    calendar = tuple(Model(period, None, transform, order, seasonal)
                     for transform in ('level', 'log_mean')
                     for order, seasonal in (((1, 0, 0), (1, 0, 0, lag)),
                                             ((0, 1, 1), (0, 0, 1, lag))))
    return original, expanded + calendar


def calendar_fit(history, spec, steps):
    training = history.iloc[-spec.window:] if spec.window else history
    lag = spec.seasonal_order[-1]
    if len(training) < 2 * lag:
        raise ValueError(f'Calendar SARIMA requires {2 * lag} complete periods')
    if (not np.isfinite(training).all()) or (training < 0).any():
        raise ValueError('Expected finite nonnegative revenue')
    if training.std() < 1e-8:
        return np.full(steps, float(training.iloc[-1]))
    scale = max(float(training.mean()), 1.)
    encoded = training / scale
    if spec.transform == 'log_mean':
        encoded = np.log1p(encoded)
    elif spec.transform != 'level':
        raise ValueError('Unknown revenue transformation')
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
            raise ValueError('Calendar SARIMA did not converge')
        prediction = fitted.get_forecast(steps=steps)
        point = np.asarray(prediction.predicted_mean)
        if spec.transform == 'log_mean':
            point = np.expm1(point + .5 * np.asarray(prediction.var_pred_mean))
    point = np.maximum(0., point * scale)
    if not np.isfinite(point).all() or (point >= 1e18).any():
        raise ValueError('Calendar SARIMA produced invalid revenue')
    return point


def walk(history, targets, spec, period, cutoff=None):
    if spec.frequency == 'daily':
        daily_spec = DailyModel(spec.window, spec.transform, spec.order, spec.seasonal_order)
        return daily_walk(history, targets, daily_spec, period, fixed_cutoff=cutoff)
    values = aggregate(history, period, PROTOCOL)
    rows = []
    for stamp, actual in targets.items():
        prefix = before(values, cutoff if cutoff is not None else stamp, period)
        trained = period_end(prefix.index[-1], period)
        try:
            steps = 1 if cutoff is None else len(values.loc[(values.index >= cutoff) & (values.index <= stamp)])
            point = float(calendar_fit(prefix, spec, steps)[-1])
            item = row(stamp, actual, trained, period, [point, point, point])
            item['lower'] = item['upper'] = None
        except (ValueError, np.linalg.LinAlgError, OverflowError) as exc:
            item = row(stamp, actual, trained, period, reason=str(exc))
        rows.append(item)
    return rows


def candidate_task(task):
    history, targets, spec, period = task
    rows = walk(history, targets, spec, period)
    return spec, rows, measured(rows)


def choose(attempts, eligible):
    valid = [item for item in attempts if item[0] in eligible and not item[2]['failed_rows']]
    if not valid:
        raise ValueError('No SARIMA candidate completed all validation origins')
    return min(valid, key=lambda item: (item[2]['mae_pesos'], item[0].values()['name']))


def fingerprint():
    return sha256(json.dumps({
        'version': VERSION, 'protocol': PROTOCOL.values(),
        'specifications': {period: [spec.values() for spec in specifications(period)[1]]
                           for period in ('monthly', 'weekly')},
    }, sort_keys=True).encode()).hexdigest()


def evaluate_period(daily, components, period, progress=None, executor=None):
    history = daily_history(daily, PROTOCOL)
    histories = {'total': history, **{name: value.reindex(history.index, fill_value=0.)
                                     for name, value in components.items()}}
    if set(components) != {'feeding', 'other_sales'}:
        raise ValueError('Expected feeding and other_sales components')
    if not np.allclose(histories['feeding'] + histories['other_sales'], history, atol=.005, rtol=0):
        raise ValueError('Component sales do not reconcile to total sales')
    actuals = {name: aggregate(value, period, PROTOCOL) for name, value in histories.items()}
    validation, test = split_periods(actuals['total'], period, PROTOCOL)
    original, expanded = specifications(period)
    choices, audit = {'existing': {}, 'segmented': {}}, {}
    for name, values in histories.items():
        tasks = [(values, actuals[name].loc[validation.index], spec, period) for spec in expanded]
        results = executor.map(candidate_task, tasks) if executor else map(candidate_task, tasks)
        attempts = []
        for index, result in enumerate(results, 1):
            attempts.append(result)
            if progress and (index % 4 == 0 or index == len(tasks)):
                progress(f'{period}/{name}: validated {index}/{len(tasks)} SARIMA candidates')
        for engine, eligible in (('existing', original), ('segmented', expanded)):
            choices[engine][name] = choose(attempts, eligible)
        audit[name] = [{'model': spec.values(), 'metrics': metrics,
                        'failure_reasons': sorted({r['reason'] for r in rows if r.get('reason')})}
                       for spec, rows, metrics in attempts]

    # Freeze every model, strategy, and range for BOTH approaches before tests.
    frozen = {}
    for engine, selected in choices.items():
        component_rows = combine({name: selected[name][1] for name in components}, validation, period)
        strategy = ('components' if measured(component_rows)['mae_pesos'] < selected['total'][2]['mae_pesos']
                    else 'direct')
        valid_rows = component_rows if strategy == 'components' else selected['total'][1]
        frozen[engine] = {
            'strategy': strategy,
            'models': {name: selected[name][0].values() for name in histories},
            'validation_metrics': measured(valid_rows),
            'component_validation_metrics': {name: selected[name][2] for name in components},
            'radius': range_radius(valid_rows),
            'validation_dates': [str(stamp.date()) for stamp in validation.index],
            'test_start': PROTOCOL.test_start, 'test_end': PROTOCOL.test_end,
        }
    selection_signature = sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()
    if progress:
        progress(f'{period}: froze both selections ({selection_signature[:12]}); scoring Jul-Dec 2025')
    cache = {}
    def predictions(name, spec, fixed=False):
        key = name, spec, fixed
        if key not in cache:
            cache[key] = walk(histories[name], actuals[name].loc[test.index], spec, period,
                              pd.Timestamp(PROTOCOL.test_start) if fixed else None)
        return cache[key]

    results = {}
    for engine, selected in choices.items():
        selection = frozen[engine]
        tested = {name: predictions(name, item[0]) for name, item in selected.items()}
        component_rows = combine({name: tested[name] for name in components}, test, period)
        rows = component_rows if selection['strategy'] == 'components' else tested['total']
        rows = add_ranges(rows, selection['radius'])
        metric = measured(rows)
        fixed = []
        if period == 'monthly':
            fixed_parts = {name: predictions(name, selected[name][0], True)
                           for name in (components if selection['strategy'] == 'components' else ['total'])}
            fixed = combine(fixed_parts, test, period) if selection['strategy'] == 'components' else fixed_parts['total']
        results[engine] = {
            'selection': selection, 'selection_signature': selection_signature,
            'metrics': metric, 'status': period_acceptance(metric), 'rows': rows,
            'component_metrics': {name: measured(tested[name]) for name in components},
            'component_rows': {name: tested[name] for name in components},
            'strategy_comparison': {'direct': measured(tested['total']), 'components': measured(component_rows)},
            'fixed_origin_rows': fixed, 'fixed_origin_metrics': measured(fixed),
            'baselines': baselines(actuals['total'], test, period, rows),
        }
    return {'approaches': results, 'candidate_audit': audit}


def evaluate(daily, components, workers=2, progress=None, completed=None, checkpoint=None):
    if daily.empty or daily.index.max() < pd.Timestamp(PROTOCOL.test_end):
        raise ValueError('Complete real sales through 2025-12-31 are required')
    pool = (ProcessPoolExecutor(max_workers=workers, mp_context=get_context('spawn'))
            if workers > 1 else nullcontext(None))
    periods = dict(completed or {})
    with pool as executor:
        for period in ('monthly', 'weekly'):
            if period in periods:
                if progress:
                    progress(f'{period}: reused completed checkpoint')
                continue
            periods[period] = evaluate_period(daily, components, period, progress, executor)
            if checkpoint:
                checkpoint(periods)
            if progress:
                progress(f'{period}: finished and checkpointed retrospective comparison')
    return {'version': VERSION, 'protocol': PROTOCOL.values(), 'limitations': LIMITATIONS,
            'source': 'real non-voided transactions only', 'periods': periods}
