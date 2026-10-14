"""Trust recorded purchases; compare aggregate and customer-based SARIMA."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import datetime, time
from hashlib import sha256
import json
from multiprocessing import get_context

import numpy as np
import pandas as pd
from django.db.models import Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from sales.models import Transaction
from .data import as_series, feeding_filter, source
from .daily_sarima import add_ranges, combine, daily_history, range_radius
from .rebuilt import aggregate, measured, period_end, split_periods
from .segmented_sarima import Model, PROTOCOL
from .tuned_sarima import candidate_task, rank, read_model, validate_candidates, walk

VERSION = 'customer-sarima-v1'
RULES = {'maximum_customers': 8, 'minimum_active_months': 9,
         'minimum_purchase_dates': 18, 'minimum_span_days': 180,
         'selection_start': '2023-01-01', 'selection_end': '2024-12-31'}
LIMITATIONS = (
    'Exploratory retrospective experiment: 2025 was previously inspected. '
    'Customer membership uses 2023-2024 purchases only; model and structure selection use Jan-Jun 2025. '
    'Jul-Dec 2025 is the shared retrospective test, with only six monthly periods. '
    'Gaps mean no recorded purchase, not a terminated relationship or missing data. '
    'No churn labels, synthetic records, removed spikes, future membership, or test-fitted corrections are used. '
    'All customers, unassigned sales, and feeding revenue remain in combined totals. '
    'An unchanged milk-feeding model isolates the effect of customer segmentation.'
)


def read_source():
    daily, real_signature, _, components = source(include_components=True)
    cutoff = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    rows = list(Transaction.objects.filter(is_voided=False, created_at__lt=cutoff,
                created_at__date__gte=RULES['selection_start']).exclude(feeding_filter()).annotate(
        day=TruncDate('created_at', tzinfo=timezone.get_current_timezone())).values(
            'day', 'customer_id', 'customer__name').annotate(revenue=Sum('total_amount')).order_by('day', 'customer_id'))
    customers = {}
    for item in rows:
        key = str(item['customer_id']) if item['customer_id'] is not None else 'unassigned'
        customer = customers.setdefault(key, {'name': item['customer__name'] or 'Unassigned', 'daily': {}})
        customer['daily'][item['day']] = item['revenue']
    encoded = {'real_signature': real_signature,
               'customers': [{key: str(value) for key, value in item.items()} for item in rows]}
    signature = sha256(json.dumps(encoded, sort_keys=True).encode()).hexdigest()
    return as_series(daily), {name: as_series(values) for name, values in components.items()}, {
        key: {'name': item['name'], 'daily': as_series(item['daily'])} for key, item in customers.items()
    }, signature, real_signature


def select_customers(customers):
    eligible, audit = [], []
    for key, customer in customers.items():
        prefix = customer['daily'].sort_index().loc[RULES['selection_start']:RULES['selection_end']]
        positive = prefix[prefix > 0]
        months = len(set(positive.index.to_period('M')))
        span = (positive.index[-1] - positive.index[0]).days if len(positive) else 0
        accepted = (key != 'unassigned' and months >= RULES['minimum_active_months']
                    and len(positive) >= RULES['minimum_purchase_dates'] and span >= RULES['minimum_span_days'])
        item = {'key': key, 'name': customer['name'], 'training_revenue': round(float(prefix.sum()), 2),
                'active_months': months, 'purchase_dates': len(positive), 'span_days': span,
                'eligible': accepted}
        audit.append(item)
        if accepted:
            eligible.append(item)
    eligible.sort(key=lambda item: (-item['training_revenue'], item['key']))
    selected = eligible[:RULES['maximum_customers']]
    keys = {item['key'] for item in selected}
    for item in audit:
        item['selected'] = item['key'] in keys
    return [item['key'] for item in selected], audit


def specifications(period, first_month):
    specs = [Model('daily', window, transform, order, seasonal)
             for window in (56, 91, 365) for transform in ('level', 'log_mean', 'log_median')
             for order, seasonal in (((1, 0, 0), (1, 0, 0, 7)), ((0, 1, 1), (0, 0, 1, 7)))]
    # A customer's pre-entry zero padding is not two years of active history.
    if period == 'monthly' and first_month is not None and first_month <= pd.Period('2023-01', freq='M'):
        specs += [Model('monthly', None, transform, order, seasonal)
                  for transform in ('level', 'log_median')
                  for order, seasonal in (((1, 0, 0), (1, 0, 0, 12)), ((0, 1, 1), (0, 0, 1, 12)))]
    return tuple(specs)


def configuration_signature():
    return sha256(json.dumps({'version': VERSION, 'rules': RULES, 'protocol': PROTOCOL.values(),
        'grid': {p: [s.values() for s in specifications(p, pd.Period('2023-01', freq='M'))]
                 for p in ('monthly', 'weekly')}, 'target': 15}, sort_keys=True).encode()).hexdigest()


def predict(histories, actuals, targets, models, period, executor=None):
    tasks = {name: (histories[name], actuals[name].loc[targets.index], model, period)
             for name, model in models.items()}
    if executor:
        pending = {executor.submit(candidate_task, task): name for name, task in tasks.items()}
        return {pending[future]: future.result()[1] for future in as_completed(pending)}
    return {name: candidate_task(task)[1] for name, task in tasks.items()}


def evaluate_period(daily, components, customers, membership, period, feeding_model,
                    executor=None, cache_dir=None, progress=None):
    total = daily_history(daily, PROTOCOL)
    feeding = components['feeding'].reindex(total.index, fill_value=0.)
    regular = components['other_sales'].reindex(total.index, fill_value=0.)
    all_customers = {key: item['daily'].reindex(total.index, fill_value=0.) for key, item in customers.items()}
    if not all_customers or not np.allclose(sum(all_customers.values()), regular, atol=.005, rtol=0):
        raise ValueError('Customer sales do not reconcile to all non-feeding revenue')
    if not np.allclose(regular + feeding, total, atol=.005, rtol=0):
        raise ValueError('Feeding and regular sales do not reconcile to total revenue')
    histories = {f'customer_{key}': all_customers[key] for key in membership}
    remainder = [values for key, values in all_customers.items() if key not in membership]
    histories['pooled_remaining'] = sum(remainder) if remainder else total * 0.
    customer_names = list(histories)
    histories.update(regular_aggregate=regular, feeding=feeding)
    actuals = {name: aggregate(value, period, PROTOCOL) for name, value in histories.items()}
    regular_actual = aggregate(regular, period, PROTOCOL)
    total_actual = aggregate(total, period, PROTOCOL)
    validation, test = split_periods(total_actual, period, PROTOCOL)
    models, validated, selections = {'feeding': feeding_model}, {}, {}
    for name, history in histories.items():
        if name == 'feeding':
            continue
        prefix = history.loc[:'2024-12-31']
        positive = prefix[prefix > 0]
        first = positive.index.min().to_period('M') if len(positive) else None
        attempts = validate_candidates(history, actuals[name].loc[validation.index], period, name,
            executor, cache_dir, progress, specs=specifications(period, first))
        model, rows, metric = rank(attempts)[0]
        models[name], validated[name] = model, rows
        selections[name] = {'model': model.values(), 'validation_metrics': metric,
            'attempts': [{'model': spec.values(), 'metrics': score, 'rows': items} for spec, items, score in attempts]}
    validated.update(predict(histories, actuals, validation, {'feeding': feeding_model}, period, executor))
    customer_valid = combine({name: validated[name] for name in customer_names}, regular_actual.loc[validation.index], period)
    plans = {'aggregate_regular': validated['regular_aggregate'], 'customer_regular': customer_valid}
    frozen = {}
    for approach, regular_rows in plans.items():
        combined = combine({'regular': regular_rows, 'feeding': validated['feeding']}, validation, period)
        frozen[approach] = {'validation_regular': measured(regular_rows), 'validation_combined': measured(combined),
                             'radius': range_radius(combined)}
    selected_approach = min(frozen, key=lambda name: (frozen[name]['validation_combined']['mae_pesos'], name))
    selection = {'membership': membership, 'models': {n: m.values() for n, m in models.items()},
                 'approaches': frozen, 'selected_approach': selected_approach,
                 'validation_dates': [str(s.date()) for s in validation.index]}
    signature = sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest()
    if progress:
        progress(f'{period}: froze {selected_approach} selection ({signature[:12]}); scoring Jul-Dec 2025')
    tested = predict(histories, actuals, test, models, period, executor)
    customer_test = combine({name: tested[name] for name in customer_names}, regular_actual.loc[test.index], period)
    approaches = {}
    for approach, rows in {'aggregate_regular': tested['regular_aggregate'], 'customer_regular': customer_test}.items():
        combined = combine({'regular': rows, 'feeding': tested['feeding']}, test, period)
        combined = add_ranges(combined, frozen[approach]['radius'])
        metrics = {'regular': measured(rows), 'feeding': measured(tested['feeding']), 'combined': measured(combined)}
        score = metrics['combined']
        approaches[approach] = {'rows': combined, 'regular_rows': rows, 'metrics': metrics,
            'below_15_percent': score['rows'] >= 5 and not score['failed_rows'] and score['wape_percent'] is not None and score['wape_percent'] < 15,
            'validation_metrics': frozen[approach]}
    return {'selection': selection, 'selection_signature': signature, 'selected_approach': selected_approach,
        'approaches': approaches, 'customer_models': selections,
        'customer_metrics': {name: measured(tested[name]) for name in customer_names},
        'customer_rows': {name: tested[name] for name in customer_names}}


def evaluate(daily, components, customers, previous, workers=4, cache_dir=None,
             progress=None, completed=None, checkpoint=None):
    if daily.empty or daily.index.max() < pd.Timestamp(PROTOCOL.test_end):
        raise ValueError('Real sales through December 2025 are required')
    membership, audit = select_customers(customers)
    if not membership:
        raise ValueError('No customers meet the training-period eligibility rules')
    if progress:
        progress('Customers selected from 2023-2024 only: ' + ', '.join(customers[k]['name'] for k in membership))
    periods = dict(completed or {})
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=get_context('spawn')) if workers > 1 else nullcontext(None)
    with pool as executor:
        for period in ('monthly', 'weekly'):
            if period not in periods:
                feeding_model = read_model(previous['periods'][period]['approaches']['existing']['selection']['models']['feeding'])
                periods[period] = evaluate_period(daily, components, customers, membership, period,
                    feeding_model, executor, cache_dir, progress)
                if checkpoint:
                    checkpoint(periods)
            if progress:
                for name, result in periods[period]['approaches'].items():
                    progress(f'{period}/{name}: regular WAPE {result["metrics"]["regular"]["wape_percent"]}%; '
                             f'combined WAPE {result["metrics"]["combined"]["wape_percent"]}%')
    return {'version': VERSION, 'protocol': PROTOCOL.values(), 'target_percent': 15,
            'rules': RULES, 'customer_selection': audit, 'membership': membership,
            'limitations': LIMITATIONS, 'periods': periods}
