"""Display saved forecasts and recorded revenue over a requested calendar.

This is a read path: it never trains, projects new values or issues forecasts.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .beta_graph import report, advance, _issued_predictions
from .benchmarks import score
from .chart import period_start, period_end, _regular_backtest
from .data import load_snapshot
from .models import GraphPrediction
from .serving import model_context
from .weekly import weekly_report

MAX_BUCKETS = 1200


def calendar(period, start, end):
    target, last = period_start(start, period), period_start(end, period)
    result = []
    while target <= last:
        if len(result) == MAX_BUCKETS:
            raise ValueError(f'Choose a range containing at most {MAX_BUCKETS} {period} periods.')
        result.append(target)
        target = advance(target, period, 1)
    return result


def historical_comparisons(period):
    """Reuse only source-validated retained research comparisons."""
    if period == 'weekly':
        return {row['date']: row['predicted'] for row in weekly_report().get('test_rows', [])}
    snapshot, _, latest, _, _, _ = model_context()
    totals = defaultdict(dict)
    for sale in snapshot.sales:
        if sale.segment == 'regular':
            month = sale.day.replace(day=1)
            totals[month]['regular'] = totals[month].get('regular', Decimal('0')) + sale.revenue
    rows, _ = _regular_backtest(latest, snapshot, totals)
    if period == 'monthly':
        return {row['month']: row['predicted'] for row in rows}
    years = defaultdict(list)
    for row in rows:
        years[row['month'][:4]].append(row)
    # An annual sum of rolling monthly tests is not a year-ahead issued forecast.
    return {year+'-01-01': round(sum(row['predicted'] for row in values), 2)
            for year, values in years.items() if len(values) == 12}


def range_report(period, scope, start_date, end_date):
    dates = calendar(period, start_date, end_date)
    today = timezone.localdate()
    current = period_start(today, period)
    data = report(period=period, scope=scope, as_of=today)
    snapshot = load_snapshot(today + timedelta(days=1))
    totals = defaultdict(dict)
    for sale in snapshot.sales:
        if sale.segment in ('regular', 'feeding') and sale.day <= today:
            target = period_start(sale.day, period)
            totals[target][sale.segment] = totals[target].get(sale.segment, Decimal('0')) + sale.revenue
    comparisons = historical_comparisons(period) if dates[0] < current else {}
    # Limit issue lookups to targets with retained records, avoiding a query for
    # every empty calendar bucket. The existing coherent-bundle checks still apply.
    issue_targets = set(GraphPrediction.objects.filter(run__scope=scope, run__period=period,
        target__gte=dates[0], target__lte=min(dates[-1], current)).values_list('target', flat=True))
    issues = {target: _issued_predictions(period, target, today) for target in issue_targets if target < current}
    for key, view in data['views'].items():
        saved = {p['date']: p for p in (data.get('seasonal_outlook') or {}).get('views', {}).get(key, {}).get('points', [])}
        saved.update({p['date']: p for p in view['points']})
        points, evaluations = [], defaultdict(list)
        for target in dates:
            day = target.isoformat()
            point = dict(saved.get(day) or {'date': day, 'end_date': period_end(target, period).isoformat(),
                'past_prediction': None, 'future_prediction': None,
                'prediction_basis': 'historical_test' if target < current else 'forecast',
                'issued_prediction': None, 'issued_provenance': None})
            values = totals.get(target, {})
            actual = sum(values.values(), Decimal('0')) if key == 'combined' and values else values.get(key)
            point.update(actual=float(actual) if actual is not None and target <= current else None,
                         is_current=target == current, is_partial_period=target == current)
            if target < current:
                if key in issues.get(target, {}):
                    issue = issues[target][key]
                    point.update(past_prediction=issue['value'] if issue else None,
                                 issued_prediction=issue['value'] if issue else None,
                                 issued_provenance=issue['provenance'] if issue else None,
                                 model_details=None)
                    if issue:
                        point.update(prediction_basis='issued_forecast',
                                     forecast_origin=issue['provenance']['forecast_origin'],
                                     horizon=issue['provenance']['horizon'])
                elif key == 'regular' and point['past_prediction'] is None and day in comparisons:
                    point.update(past_prediction=comparisons[day], prediction_basis='historical_test')
                    if period == 'yearly':
                        point['outlook_basis'] = 'sum_of_12_monthly_rolling_predictions'
                predicted = point['past_prediction']
                if predicted is not None and point['actual'] is not None:
                    evaluations[point['prediction_basis']].append({'actual': point['actual'], 'predicted': predicted})
            points.append(point)
        count = sum(p['past_prediction'] is not None or p['future_prediction'] is not None for p in points)
        view.update(points=points, evaluation={basis: score(rows) for basis, rows in evaluations.items()},
                    readiness={**view.get('readiness', {}), 'available_prediction_points': count,
                        'expected_prediction_points': len(points),
                        'status': 'available' if count == len(points) else 'partially_available' if count else 'unavailable'},
                    message=None if count else 'No saved estimates are available for these dates.')
    data.update(requested_range={'start_date': start_date.isoformat(), 'end_date': end_date.isoformat()},
                range={'start_date': dates[0].isoformat(), 'end_date': period_end(dates[-1], period).isoformat(),
                       'past_periods': sum(d < current for d in dates), 'future_periods': sum(d > current for d in dates)},
                seasonal_outlook=None)
    counts = [v['readiness']['available_prediction_points'] for v in data['views'].values()]
    data['readiness'] = {**data.get('readiness', {}), 'available_prediction_points': sum(counts),
        'expected_prediction_points': len(dates)*len(counts),
        'status': 'available' if all(n == len(dates) for n in counts) else 'partially_available' if any(counts) else 'unavailable'}
    return data
