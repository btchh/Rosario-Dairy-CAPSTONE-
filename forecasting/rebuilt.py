"""Chronological SARIMA selection, reconciled component models, and backtests."""
from hashlib import sha256
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import nullcontext
from multiprocessing import get_context
import json

import numpy as np
import pandas as pd

from .contract import VERSION, LIMITATIONS, Options, period_acceptance
from .metrics import score
from .sarima import candidates, forecast


def period_end(stamp, period):
    if period == 'monthly':
        return stamp + pd.offsets.MonthEnd(0)
    if period == 'weekly':
        return stamp + pd.Timedelta(days=6)
    raise ValueError('Expected monthly or weekly period')


def aggregate(daily, period, options=None):
    options = options or Options()
    if daily.empty:
        raise ValueError('No completed sales are available')
    if (not isinstance(daily.index, pd.DatetimeIndex) or daily.index.has_duplicates
            or daily.index.tz is not None or not daily.index.equals(daily.index.normalize())):
        raise ValueError('Expected unique local business dates')
    known = daily.sort_index().astype(float)
    if not np.isfinite(known).all() or (known < 0).any():
        raise ValueError('Revenue must be finite and nonnegative')
    start = pd.Timestamp(options.training_start)
    if known.index.max() < start:
        raise ValueError('No sales in the training coverage')
    # Imported workbooks establish coverage through 2025. A newly recorded
    # sale cannot retroactively certify empty intervening live-ledger months.
    counts = known.resample('MS').count()
    unknown = counts[(counts.index > pd.Timestamp('2025-12-31')) & (counts == 0)
                     & (period_end(counts.index, 'monthly') <= known.index.max())]
    if len(unknown):
        raise ValueError(f'Unverified sales coverage for {unknown.index[0]:%Y-%m}; do not train it as zero revenue')
    # Within the imported workbook coverage this models recorded revenue.
    # An absent ticket is not evidence that the business was closed.
    days = known.reindex(pd.date_range(start, known.index.max()), fill_value=0.)
    rule = {'monthly': 'MS', 'weekly': 'W-MON'}.get(period)
    if not rule:
        raise ValueError('Expected monthly or weekly period')
    values = days.resample(rule, label='left', closed='left').sum().round(2)
    return values[(values.index >= start) & (period_end(values.index, period) <= known.index.max())]


def split_periods(values, period, options):
    ends = period_end(values.index, period)
    validation = values[(values.index >= pd.Timestamp(options.validation_start))
                        & (ends < pd.Timestamp(options.test_start))]
    if period == 'weekly' and len(validation):
        dates = sorted(set(validation.index[::options.weekly_validation_stride]) | {validation.index[-1]})
        validation = validation.loc[dates]
    test = values[(values.index >= pd.Timestamp(options.test_start))
                  & (ends <= pd.Timestamp(options.test_end))]
    if len(validation) < 5 or len(test) < 5:
        raise ValueError(f'Insufficient complete {period} validation/test periods')
    return validation, test


def before(values, stamp, period):
    return values[period_end(values.index, period) < stamp]


def row(stamp, actual, trained_through, period, prediction=None, reason=None):
    return {'date': str(stamp.date()), 'end_date': str(period_end(stamp, period).date()),
            'trained_through': str(trained_through.date()), 'actual': round(float(actual), 2),
            'predicted': round(float(prediction[0]), 2) if prediction is not None else None,
            'lower': round(float(prediction[1]), 2) if prediction is not None else None,
            'upper': round(float(prediction[2]), 2) if prediction is not None else None,
            **({'reason': reason} if reason else {})}


def measured(rows):
    return {**score(rows), 'attempted_rows': len(rows),
            'failed_rows': sum(item['predicted'] is None for item in rows)}


def walk(values, targets, spec, period, stop_on_error=False):
    rows = []
    for stamp, actual in targets.items():
        history = before(values, stamp, period)
        trained_through = period_end(history.index[-1], period)
        try:
            path = forecast(history, spec)
            rows.append(row(stamp, actual, trained_through, period, [part[0] for part in path]))
        except (ValueError, np.linalg.LinAlgError, OverflowError) as exc:
            rows.append(row(stamp, actual, trained_through, period, reason=str(exc)))
            if stop_on_error:
                break
    return rows


def loss(metrics):
    # When validation revenue is entirely zero, percentage error is undefined.
    return metrics['wape_percent'] if metrics['wape_percent'] is not None else metrics['mae_pesos']


def candidate_rows(values, validation, specs, period, executor):
    if executor is None:
        for index, spec in enumerate(specs):
            yield index, walk(values, validation, spec, period, stop_on_error=True)
        return
    pending = {executor.submit(walk, values, validation, spec, period, True): index
               for index, spec in enumerate(specs)}
    for future in as_completed(pending):
        yield pending[future], future.result()


def select(values, validation, period, label, progress, executor=None):
    prefix = before(values, validation.index[0], period)
    specs, evidence = candidates(prefix, period)
    results, ranked = [None] * len(specs), []
    for completed, (index, rows) in enumerate(candidate_rows(values, validation, specs, period, executor), 1):
        spec = specs[index]
        metrics = measured(rows)
        complete = len(rows) == len(validation) and metrics['failed_rows'] == 0
        attempt = {'specification': spec.values(), 'metrics': metrics,
                   'status': 'evaluated' if complete else 'unavailable',
                   'required_rows': len(validation), 'rows': rows}
        results[index] = attempt
        if complete:
            ranked.append((loss(metrics), spec.name, spec, rows, metrics))
        if progress and (completed % 4 == 0 or completed == len(specs)):
            progress(f'{period}/{label}: validated {completed}/{len(specs)} candidate specifications')
    if not ranked:
        raise ValueError(f'No {period}/{label} candidate completed every validation origin')
    _, _, chosen, rows, metrics = min(ranked, key=lambda item: (item[0], item[1]))
    return chosen, rows, {'model': chosen.values(), 'metrics': metrics, 'diagnostics': evidence,
                          'attempts': results, 'attempted_candidates': len(specs)}


def combine_rows(parts, actuals, period):
    rows = []
    for index, (stamp, actual) in enumerate(actuals.items()):
        source = [items[index] for items in parts.values()]
        if any(item['date'] != str(stamp.date()) for item in source):
            raise ValueError('Component prediction dates do not align')
        complete = all(item['predicted'] is not None for item in source)
        prediction = [sum(item[key] for item in source) for key in ('predicted', 'lower', 'upper')] if complete else None
        rows.append(row(stamp, actual, pd.Timestamp(max(item['trained_through'] for item in source)),
                        period, prediction, None if complete else 'A component fit was unavailable'))
    return rows


def baselines(values, targets, period, model_rows):
    seasonal_lag = 12 if period == 'monthly' else 52
    recent = 3 if period == 'monthly' else 13
    rows = {name: [] for name in ('previous_period', 'recent_mean', 'seasonal_naive')}
    for stamp, actual in targets.items():
        history = before(values, stamp, period)
        predictions = {'previous_period': float(history.iloc[-1]),
                       'recent_mean': float(history.iloc[-recent:].mean()),
                       'seasonal_naive': float(history.iloc[-seasonal_lag]) if len(history) >= seasonal_lag else None}
        for name, prediction in predictions.items():
            rows[name].append({'date': str(stamp.date()), 'actual': float(actual), 'predicted': prediction})
    result = {}
    for name, items in rows.items():
        shared = [(model, baseline) for model, baseline in zip(model_rows, items)
                  if model['predicted'] is not None and baseline['predicted'] is not None]
        model_metric = score([item[0] for item in shared])
        baseline_metric = score([item[1] for item in shared])
        result[name] = {'metrics': measured(items), 'matched_rows': len(shared),
                        'matched_model_metrics': model_metric, 'matched_baseline_metrics': baseline_metric,
                        'beats_baseline': (model_metric['wape_percent'] < baseline_metric['wape_percent'])
                        if model_metric['wape_percent'] is not None and baseline_metric['wape_percent'] is not None else None}
    return result


def predict_path(series, plan, steps):
    paths = [forecast(series[name], spec, steps) for name, spec in plan.items()]
    return tuple(np.sum([path[index] for path in paths], axis=0) for index in range(3))


def fixed_origin(series, plan, targets, period, cutoff):
    histories = {name: before(values, cutoff, period) for name, values in series.items()}
    last = histories['total'].index[-1]
    frequency = 'MS' if period == 'monthly' else 'W-MON'
    dates = pd.date_range(last, targets.index[-1], freq=frequency)[1:]
    trained_through = period_end(last, period)
    try:
        path = predict_path(histories, plan, len(dates))
        return [row(stamp, actual, trained_through, period,
                    [part[dates.get_loc(stamp)] for part in path]) for stamp, actual in targets.items()]
    except (ValueError, np.linalg.LinAlgError, OverflowError) as exc:
        return [row(stamp, actual, trained_through, period, reason=str(exc)) for stamp, actual in targets.items()]


def next_forecast(series, plan, period, as_of, method):
    values = series['total']
    last = period_end(values.index[-1], period)
    start = last + pd.Timedelta(days=1)
    base = {'status': 'incomplete_current_period', 'method': method, 'trained_through': str(last.date())}
    # A one-step backtest cannot certify a two-step forecast that skips the
    # partially observed period. Preserve the evaluated horizon.
    if start <= as_of:
        return base
    try:
        point, lower, upper = predict_path(series, plan, 1)
    except (ValueError, np.linalg.LinAlgError, OverflowError) as exc:
        return {**base, 'status': 'model_unavailable', 'reason': str(exc)}
    end = period_end(start, period)
    return {**base, 'status': 'ready', 'date': str(start.date()), 'end_date': str(end.date()),
            'days': (end - start).days + 1,
            'combined': {'predicted_revenue': round(float(point[0]), 2),
                         'lower_bound': round(float(lower[0]), 2), 'upper_bound': round(float(upper[0]), 2),
                         'range_kind': 'native_marginal_80_percent' if len(plan) == 1 else 'sum_of_component_bounds_not_calibrated'}}


def evaluate_period(daily, period, progress=None, options=None, components=None, executor=None):
    options = options or Options()
    values = aggregate(daily, period, options)
    validation, test = split_periods(values, period, options)
    series = {'total': values}
    if components:
        dates = pd.date_range(options.training_start, daily.index.max())
        for name, component in components.items():
            component = component.reindex(dates, fill_value=0.)
            series[name] = aggregate(component, period, options)
        if not np.allclose(sum(series[name] for name in components), values, atol=.005, rtol=0):
            raise ValueError('Component sales do not reconcile to total sales')
    models, validation_rows, selection = {}, {}, {}
    for name, value in series.items():
        models[name], validation_rows[name], selection[name] = select(
            value, value.loc[validation.index], period, name, progress, executor)
    direct_metric = measured(validation_rows['total'])
    strategy = 'direct'
    validation_metric = direct_metric
    combined_validation = None
    if components:
        combined_validation = combine_rows({name: validation_rows[name] for name in components}, validation, period)
        combined_metric = measured(combined_validation)
        if not combined_metric['failed_rows'] and loss(combined_metric) < loss(direct_metric):
            strategy, validation_metric = 'components', combined_metric
    plan = {'total': models['total']} if strategy == 'direct' else {name: models[name] for name in components}
    method = models['total'].name if strategy == 'direct' else 'sum_of_customer_segment_sarima'
    selection_record = {'strategy': strategy, 'models': selection,
                        'validation_metrics': validation_metric,
                        'component_validation_metrics': measured(combined_validation) if combined_validation else None,
                        'validation_dates': [str(stamp.date()) for stamp in validation.index],
                        'selection_trained_through': str(period_end(validation.index[-1], period).date())}
    selection_signature = sha256(json.dumps(selection_record, sort_keys=True, allow_nan=False).encode()).hexdigest()
    if progress:
        progress(f'{period}: froze {strategy} selection ({selection_signature[:12]}); now scoring retrospective 2025 predictions')
    tested = {name: walk(value, value.loc[test.index], models[name], period) for name, value in series.items()}
    component_rows = combine_rows({name: tested[name] for name in components}, test, period) if components else None
    rows = tested['total'] if strategy == 'direct' else component_rows
    metric = measured(rows)
    fixed = fixed_origin(series, plan, test, period, pd.Timestamp(options.test_start)) if period == 'monthly' else []
    result = {'method': method, 'strategy': strategy, 'validation_metrics': validation_metric,
              'selection': selection_record, 'selection_signature': selection_signature,
              'rows': rows, 'metrics': {'combined': metric}, 'status': period_acceptance(metric),
              'fixed_origin_rows': fixed, 'fixed_origin_metrics': measured(fixed),
              'baselines': baselines(values, test, period, rows),
              'component_metrics': {name: measured(tested[name]) for name in components or {}},
              'component_comparison': {name: tested[name] for name in components or {}},
              'strategy_comparison': {'direct': measured(tested['total']),
                                      'components': measured(component_rows) if component_rows else None},
              'validation_start': str(validation.index[0].date()),
              'validation_end': str(period_end(validation.index[-1], period).date()),
              'test_start': str(test.index[0].date()), 'test_end': str(period_end(test.index[-1], period).date()),
              'next_period': next_forecast(series, plan, period, daily.index.max(), method)}
    return result


def evaluate(daily, options=None, progress=None, components=None, workers=2):
    options = options or Options()
    if daily.empty or daily.index.max() < pd.Timestamp(options.test_end):
        raise ValueError('The configured test coverage is not yet available')
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=get_context('spawn')) if workers > 1 else nullcontext(None)
    with pool as executor:
        periods = {period: evaluate_period(daily, period, progress, options, components, executor)
                   for period in ('monthly', 'weekly')}
    monthly = periods['monthly']['fixed_origin_rows']
    complete = len(monthly) == 12 and all(item['predicted'] is not None for item in monthly)
    annual = {'date': options.test_start, 'end_date': options.test_end,
              'trained_through': monthly[0]['trained_through'],
              'actual': round(sum(item['actual'] for item in monthly), 2),
              **{key: round(sum(item[key] for item in monthly), 2) if complete else None
                 for key in ('predicted', 'lower', 'upper')},
              'evaluation_kind': 'fixed_origin_monthly_path'}
    yearly_metric = measured([annual])
    periods['yearly'] = {'method': periods['monthly']['method'] + '_annual_sum',
                         'rows': [annual], 'metrics': {'combined': yearly_metric},
                         'status': period_acceptance(yearly_metric), 'validation_metrics': None,
                         'baselines': {}, 'next_period': {'status': 'insufficient_evaluation'}}
    breakdown = {}
    for year in sorted(set(daily.index.year)):
        if year < pd.Timestamp(options.training_start).year:
            continue
        breakdown[str(year)] = {'total': round(float(daily[daily.index.year == year].sum()), 2),
                                **{name: round(float(value[value.index.year == year].sum()), 2)
                                   for name, value in (components or {}).items()}}
    return {'version': VERSION, 'selected_setup': 'sarima', 'selection_status': 'selected',
            'selection_trained_through': str((pd.Timestamp(options.test_start) - pd.Timedelta(days=1)).date()),
            'configuration': options.values(), 'data_start': str(daily.index.min().date()),
            'data_end': str(daily.index.max().date()),
            'data_provenance': {'source': 'non-voided transactions',
                                'model_training_start': options.training_start,
                                'component_rule': 'Sale-time customer label contains feeding; every other transaction stays in other_sales',
                                'yearly_revenue_breakdown': breakdown,
                                'unrecorded_days': 'zero recorded revenue; closure is not inferred',
                                'evaluation_kind': 'retrospective; 2025 was previously inspected'},
            'setups': {'sarima': {'setup': 'sarima', 'periods': periods}}, 'limitations': LIMITATIONS}
