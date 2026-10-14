"""Bounded, auditable real-sales SARIMA tuning, including joint segment choice."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import nullcontext
from hashlib import sha256
import json
from multiprocessing import get_context
from pathlib import Path
import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX
from threadpoolctl import threadpool_limits

from .daily_sarima import add_ranges, combine, daily_history, range_radius
from .rebuilt import aggregate, baselines, before, measured, period_end, row, split_periods
from .segmented_sarima import Model, PROTOCOL, specifications as previous_specifications

VERSION = 'tuned-sarima-v1'
TARGET = 25.0
STRETCH_TARGET = 20.0
SHORTLIST = 5
LIMITATIONS = (
    'Exploratory retrospective tuning: 2025 has already been inspected and informed development. '
    'Settings and component pairs are chosen on Jan-Jun 2025, then frozen before scoring Jul-Dec 2025. '
    'Six monthly test points do not establish future accuracy. All real sales, including feeding, remain in total WAPE. '
    'No synthetic revenue, deleted spikes, future contracts, future labels, or test-fitted adjustment is used. '
    'Daily seasonality is seven days; calendar seasonality is 12 months or 52 weeks with two complete cycles required. '
    'Pre-program feeding zeros are recorded-revenue history, not evidence of contract seasonality.'
)


def specifications(period):
    previous = previous_specifications(period)[1]
    result = list(previous)
    for spec in previous:
        if spec.transform == 'level':
            result.append(Model(spec.frequency, spec.window, 'log_median', spec.order, spec.seasonal_order))
    for window in (28, 730):
        for transform in ('level', 'log_mean', 'log_median'):
            for order, seasonal in (((1, 0, 0), (1, 0, 0, 7)), ((0, 1, 1), (0, 0, 1, 7))):
                result.append(Model('daily', window, transform, order, seasonal))
    for window in (91, 365):
        for transform in ('level', 'log_median'):
            for order, seasonal in (((0, 0, 1), (1, 0, 0, 7)), ((0, 1, 1), (0, 1, 1, 7))):
                result.append(Model('daily', window, transform, order, seasonal))
    lag = {'monthly': 12, 'weekly': 52}[period]
    for transform in ('level', 'log_mean', 'log_median'):
        result.append(Model(period, None, transform, (0, 1, 1), (0, 1, 1, lag)))
        for order, seasonal in (((1, 0, 0), (1, 0, 0, lag)), ((0, 1, 1), (0, 0, 1, lag))):
            result.append(Model(period, 2 * lag, transform, order, seasonal))
    return tuple(dict.fromkeys(result))


def fit(history, spec, steps):
    training = history.iloc[-spec.window:] if spec.window else history
    minimum = 28 if spec.frequency == 'daily' else 2 * spec.seasonal_order[-1]
    if steps < 1 or len(training) < minimum:
        raise ValueError(f'SARIMA needs {minimum} complete {spec.frequency} observations')
    if not np.isfinite(training).all() or (training < 0).any():
        raise ValueError('Expected finite nonnegative recorded revenue')
    if training.std() < 1e-8:
        return np.full(steps, float(training.iloc[-1]))
    scale = max(float(training.mean()), 1.)
    encoded = training / scale
    if spec.transform in ('log_mean', 'log_median'):
        encoded = np.log1p(encoded)
    elif spec.transform != 'level':
        raise ValueError('Unknown revenue transformation')
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter('ignore')
        fitted = SARIMAX(encoded, order=spec.order, seasonal_order=spec.seasonal_order,
                         trend='n' if spec.order[1] + spec.seasonal_order[1] else 'c',
                         enforce_stationarity=True, enforce_invertibility=True).fit(
                             disp=False, maxiter=100, cov_type='none')
        if not fitted.mle_retvals.get('converged', False):
            fitted = fitted.model.fit(start_params=fitted.params, method='powell',
                                      disp=False, maxiter=60, maxfun=1500, cov_type='none')
        if not fitted.mle_retvals.get('converged', False):
            raise ValueError('SARIMA did not converge')
        forecast = fitted.get_forecast(steps=steps)
        point = np.asarray(forecast.predicted_mean)
        if spec.transform == 'log_mean':
            point = np.expm1(point + .5 * np.asarray(forecast.var_pred_mean))
        elif spec.transform == 'log_median':
            point = np.expm1(point)
    point = np.maximum(0., point * scale)
    if not np.isfinite(point).all() or (point >= 1e18).any():
        raise ValueError('SARIMA produced invalid revenue')
    return point


def walk(history, targets, spec, period, cutoff=None):
    values = history if spec.frequency == 'daily' else aggregate(history, period, PROTOCOL)
    rows = []
    fixed = None
    for stamp, actual in targets.items():
        origin = pd.Timestamp(cutoff) if cutoff is not None else stamp
        prefix = values[values.index < origin] if spec.frequency == 'daily' else before(values, origin, period)
        trained = prefix.index[-1] if spec.frequency == 'daily' else period_end(prefix.index[-1], period)
        try:
            if spec.frequency == 'daily':
                end = period_end(stamp, period)
                if cutoff is not None:
                    if fixed is None:
                        last = period_end(targets.index[-1], period)
                        fixed = pd.Series(fit(prefix, spec, (last - trained).days),
                                          index=pd.date_range(trained + pd.Timedelta(days=1), last))
                    point = float(fixed.loc[stamp:end].sum())
                else:
                    point = float(fit(prefix, spec, (end - stamp).days + 1).sum())
            else:
                steps = 1 if cutoff is None else int(((values.index >= origin) & (values.index <= stamp)).sum())
                point = float(fit(prefix, spec, steps)[-1])
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


def read_model(value):
    return Model(value['frequency'], value['window'], value['transform'],
                 tuple(value['order']), tuple(value['seasonal_order']))


def configuration_signature():
    return sha256(json.dumps({'version': VERSION, 'protocol': PROTOCOL.values(),
        'shortlist': SHORTLIST, 'target': TARGET, 'stretch_target': STRETCH_TARGET,
        'grid': {period: [model.values() for model in specifications(period)]
                 for period in ('weekly', 'monthly')}}, sort_keys=True).encode()).hexdigest()


def validate_candidates(history, targets, period, label, executor=None, cache_dir=None, progress=None, specs=None):
    # Cache identity ends at validation. Test values cannot affect these results.
    cutoff = period_end(targets.index[-1], period)
    prefix = history.loc[:cutoff]
    identity = sha256(json.dumps({'version': VERSION, 'period': period, 'dates': [str(s.date()) for s in targets.index],
        'history': [(str(stamp.date()), float(value)) for stamp, value in prefix.items()]}, sort_keys=True).encode()).hexdigest()
    attempts, missing = {}, []
    specs = tuple(specs) if specs is not None else specifications(period)
    folder = Path(cache_dir) / period / label / identity if cache_dir else None
    if folder:
        folder.mkdir(parents=True, exist_ok=True)
    def path(spec):
        return folder / (sha256(spec.values()['name'].encode()).hexdigest() + '.json')
    for spec in specs:
        if folder and path(spec).exists():
            try:
                saved = json.loads(path(spec).read_text())
                attempts[spec] = spec, saved['rows'], measured(saved['rows'])
                continue
            except (ValueError, KeyError, TypeError):
                pass
        missing.append(spec)
    if progress:
        progress(f'{period}/{label}: {len(attempts)} cached, {len(missing)} candidates to validate')
    tasks = [(history, targets, spec, period) for spec in missing]
    if executor:
        pending = [executor.submit(candidate_task, task) for task in tasks]
        results = (future.result() for future in as_completed(pending))
    else:
        results = map(candidate_task, tasks)
    for spec, rows, metrics in results:
        attempts[spec] = spec, rows, metrics
        if folder:
            temporary = path(spec).with_suffix('.tmp')
            temporary.write_text(json.dumps({'rows': rows}, allow_nan=False) + '\n')
            temporary.replace(path(spec))
        if progress and (len(attempts) % 5 == 0 or len(attempts) == len(specs)):
            progress(f'{period}/{label}: validated {len(attempts)}/{len(specs)} candidates')
    return [attempts[spec] for spec in specs]


def rank(attempts):
    good = [item for item in attempts if not item[2]['failed_rows']]
    if not good:
        raise ValueError('No candidate completed every validation origin')
    return sorted(good, key=lambda item: (item[2]['mae_pesos'], item[0].values()['name']))


def select_plan(attempts, targets, period, previous):
    ranked = {name: rank(items) for name, items in attempts.items()}
    choices = {name: list(items[:SHORTLIST]) for name, items in ranked.items() if name != 'total'}
    # Retain earlier component plans, even if outside a new component shortlist.
    for approach in previous['approaches'].values():
        for name in choices:
            spec = read_model(approach['selection']['models'][name])
            found = next((item for item in ranked[name] if item[0] == spec), None)
            if found and all(item[0] != spec for item in choices[name]):
                choices[name].append(found)
    spec, rows, _ = ranked['total'][0]
    plans = [({'total': spec}, rows)]
    for feeding in choices['feeding']:
        for other in choices['other_sales']:
            combined = combine({'feeding': feeding[1], 'other_sales': other[1]}, targets, period)
            plans.append(({'feeding': feeding[0], 'other_sales': other[0]}, combined))
    def key(item):
        plan, rows = item
        return measured(rows)['mae_pesos'], json.dumps({n: s.values() for n, s in plan.items()}, sort_keys=True)
    plan, rows = min(plans, key=key)
    selection = {'strategy': 'direct' if 'total' in plan else 'joint_components',
        'models': {name: spec.values() for name, spec in plan.items()},
        'validation_metrics': measured(rows), 'radius': range_radius(rows),
        'validation_rows': rows, 'validation_dates': [str(stamp.date()) for stamp in targets.index],
        'shortlist_per_component': SHORTLIST, 'plans_compared': len(plans),
        'objective': 'pooled total-sales validation MAE (equivalent ranking to WAPE)',
        'test_start': PROTOCOL.test_start, 'test_end': PROTOCOL.test_end}
    return plan, selection, ranked


def target_status(metric):
    if metric['failed_rows']:
        return 'model_unavailable'
    if metric['rows'] < 5 or metric['wape_percent'] is None:
        return 'insufficient_evaluation'
    return 'target_met' if metric['wape_percent'] < TARGET else 'target_not_met'


def evaluate_period(daily, components, period, previous, executor=None, cache_dir=None, progress=None):
    history = daily_history(daily, PROTOCOL)
    if set(components) != {'feeding', 'other_sales'}:
        raise ValueError('Expected feeding and other_sales components')
    histories = {'total': history, **{name: series.reindex(history.index, fill_value=0.)
                                     for name, series in components.items()}}
    if not np.allclose(histories['feeding'] + histories['other_sales'], history, atol=.005, rtol=0):
        raise ValueError('Component sales do not reconcile to total sales')
    actuals = {name: aggregate(value, period, PROTOCOL) for name, value in histories.items()}
    validation, test = split_periods(actuals['total'], period, PROTOCOL)
    attempts = {name: validate_candidates(value, actuals[name].loc[validation.index], period, name,
                                         executor, cache_dir, progress) for name, value in histories.items()}
    plan, selection, ranked = select_plan(attempts, validation, period, previous)
    signature = sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest()
    if progress:
        progress(f'{period}: froze {selection["strategy"]} among {selection["plans_compared"]} plans; '
                 f'validation WAPE {selection["validation_metrics"]["wape_percent"]}%; now scoring Jul-Dec 2025')
    # Only the frozen selected plan is scored. No test-based candidate ranking.
    tested = {name: walk(histories[name], actuals[name].loc[test.index], spec, period) for name, spec in plan.items()}
    rows = tested['total'] if 'total' in plan else combine(tested, test, period)
    rows = add_ranges(rows, selection['radius'])
    metric = measured(rows)
    fixed = []
    if period == 'monthly':
        parts = {name: walk(histories[name], actuals[name].loc[test.index], spec, period,
                            pd.Timestamp(PROTOCOL.test_start)) for name, spec in plan.items()}
        fixed = parts['total'] if 'total' in plan else combine(parts, test, period)
    # Always score the independently selected segment models for diagnosis too.
    individual = {}
    for name in components:
        spec = ranked[name][0][0]
        individual[name] = tested[name] if name in plan and plan[name] == spec else walk(
            histories[name], actuals[name].loc[test.index], spec, period)
    return {'selection': selection, 'selection_signature': signature, 'rows': rows,
        'metrics': metric, 'status': target_status(metric),
        'stretch_target_met': target_status(metric) == 'target_met' and metric['wape_percent'] < STRETCH_TARGET,
        'component_rows': {name: items for name, items in tested.items() if name != 'total'},
        'component_metrics': {name: measured(items) for name, items in tested.items() if name != 'total'},
        'individual_component_metrics': {name: measured(items) for name, items in individual.items()},
        'individual_component_models': {name: ranked[name][0][0].values() for name in components},
        'fixed_origin_rows': fixed, 'fixed_origin_metrics': measured(fixed),
        'baselines': baselines(actuals['total'], test, period, rows),
        'previous_results': previous['approaches'],
        'candidate_audit': {name: [{'model': spec.values(), 'metrics': metrics, 'rows': rows}
                                  for spec, rows, metrics in items] for name, items in attempts.items()}}


def evaluate(daily, components, previous, workers=4, cache_dir=None, progress=None, completed=None, checkpoint=None):
    if daily.empty or daily.index.max() < pd.Timestamp(PROTOCOL.test_end):
        raise ValueError('Complete real sales through December 2025 are required')
    pool = (ProcessPoolExecutor(max_workers=workers, mp_context=get_context('spawn'))
            if workers > 1 else nullcontext(None))
    periods = dict(completed or {})
    with pool as executor:
        for period in ('monthly', 'weekly'):
            if period not in periods:
                periods[period] = evaluate_period(daily, components, period, previous['periods'][period],
                                                  executor, cache_dir, progress)
                if checkpoint:
                    checkpoint(periods)
            if progress:
                progress(f'{period}: test WAPE {periods[period]["metrics"]["wape_percent"]}%; '
                         f'{periods[period]["status"]}')
    return {'version': VERSION, 'protocol': PROTOCOL.values(), 'target_percent': TARGET,
            'stretch_target_percent': STRETCH_TARGET, 'limitations': LIMITATIONS,
            'source': 'real non-voided transactions only', 'periods': periods}
