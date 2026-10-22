"""Three line-chart views of actual invoice revenue and retained SARIMA evidence."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import math

from .benchmarks import score
from .learning import shift_month, month_end
from .seasonal import SARIMA_CONFIGS
from .serving import model_context, issued_point
from .weekly import weekly_snapshot, weekly_report
from .provisional import report as provisional_report


def _regular_backtest(run, snapshot, totals):
    """Withhold an entire old/inconsistent comparison instead of mixing revisions."""
    if run is None or run.source_revision != snapshot.revision or run.status != 'research_candidate':
        return [], 'unavailable_or_source_changed'
    findings = run.findings
    name = findings.get('selected_candidate')
    rows = findings.get('candidates', {}).get(name, {}).get('test_rows', [])
    if name not in SARIMA_CONFIGS or not rows:
        return [], 'unavailable'
    seen = set()
    for row in rows:
        try:
            month = date.fromisoformat(row['month'])
            origin = date.fromisoformat(row['trained_through'])
            actual, predicted = float(row['actual']), float(row['predicted'])
            recorded = totals.get(month, {}).get('regular')
            if (month.day != 1 or origin.day != 1 or shift_month(origin) != month
                    or month in seen or recorded is None or not math.isfinite(actual)
                    or not math.isfinite(predicted) or predicted < 0
                    or abs(actual - float(recorded)) > .005):
                return [], 'inconsistent_saved_comparison'
            seen.add(month)
        except (KeyError, ValueError, TypeError, OverflowError):
            return [], 'inconsistent_saved_comparison'
    return sorted(rows, key=lambda row: row['month']), 'available'


PERIODS = ('weekly', 'monthly', 'yearly')


def period_start(day, period):
    if period == 'weekly':
        return day - timedelta(days=day.weekday())
    if period == 'yearly':
        return day.replace(month=1, day=1)
    return day.replace(day=1)


def period_end(day, period):
    if period == 'weekly':
        return period_start(day, period) + timedelta(days=6)
    if period == 'yearly':
        return day.replace(month=12, day=31)
    return month_end(day)


def chart_report(period='monthly'):
    if period not in PERIODS:
        raise ValueError('Unsupported chart period.')
    snapshot, run, latest, gate, issued, status = model_context()
    weekly = None
    if period == 'weekly':
        snapshot = weekly_snapshot()
        weekly = weekly_report(snapshot)
    totals = defaultdict(dict)
    period_totals = defaultdict(dict)
    period_counts = defaultdict(lambda: defaultdict(int))
    for sale in snapshot.sales:
        if sale.segment not in ('regular', 'feeding'):
            continue
        month = sale.day.replace(day=1)
        totals[month][sale.segment] = totals[month].get(sale.segment, Decimal('0')) + sale.revenue
        bucket = period_start(sale.day, period)
        period_totals[bucket][sale.segment] = period_totals[bucket].get(sale.segment, Decimal('0')) + sale.revenue
        period_counts[bucket][sale.segment] += 1
    calendar = []
    if period_totals:
        bucket, last = min(period_totals), max(period_totals)
        while bucket <= last:
            calendar.append(bucket)
            bucket = (bucket + timedelta(days=7) if period == 'weekly' else
                      date(bucket.year+1, 1, 1) if period == 'yearly' else shift_month(bucket))
    folds, comparison_status = _regular_backtest(latest, snapshot, totals)
    predictions = {}
    prediction_basis = 'monthly_one_step_backtest'
    if period == 'monthly':
        predictions = {row['month']: row['predicted'] for row in folds}
    elif period == 'yearly':
        by_year = defaultdict(list)
        for row in folds:
            by_year[date.fromisoformat(row['month']).year].append(row)
        # Each constituent prediction has a different monthly origin. This sum
        # must never be presented as a forecast issued before the year began.
        predictions = {date(year, 1, 1).isoformat(): round(sum(r['predicted'] for r in rows), 2)
            for year, rows in by_year.items() if len(rows) == 12}
        comparison_status = 'available_aggregated' if predictions else 'incomplete_monthly_predictions'
        prediction_basis = 'sum_of_12_monthly_rolling_predictions'
    else:
        weekly_folds = weekly.get('test_rows', [])
        predictions = {r['date']: r['predicted'] for r in weekly_folds}
        comparison_status = 'available' if weekly_folds else 'weekly_model_not_available'
        prediction_basis = 'weekly_one_step_backtest' if weekly_folds else None
        if weekly_folds:
            folds = weekly_folds
        else:
            folds = []
    views = {}
    for key, label in [('combined', 'Combined'), ('regular', 'Regular sales'), ('feeding', 'Milk feeding')]:
        history = []
        for bucket in calendar:
            values = period_totals.get(bucket, {})
            value = sum(values.values(), Decimal('0')) if key == 'combined' and values else values.get(key)
            count = sum(period_counts[bucket].values()) if key == 'combined' else period_counts[bucket][key]
            end = period_end(bucket, period)
            history.append({'date': bucket.isoformat(),
                'end_date': min(end, snapshot.as_of-timedelta(days=1)).isoformat(),
                'is_partial_period': end >= snapshot.as_of,
                'actual': float(value) if value is not None else None,
                'predicted': predictions.get(bucket.isoformat()) if key == 'regular' else None,
                'invoice_count': count, 'record_status': 'recorded' if count else 'no_invoice_recorded'})
        views[key] = {'label': label, 'history': history, 'forecast': [], 'metrics': None,
            'historical_prediction_status': comparison_status if key == 'regular' else 'not_modelled',
            'prediction_basis': prediction_basis if key == 'regular' else None,
            'status': (status if period == 'monthly' else 'horizon_not_validated') if key == 'regular'
                else 'component_unavailable' if key == 'combined' else 'not_modelled',
            'status_message': {'combined': 'Combined predictions require validated regular and feeding components.',
                'regular': 'Historical SARIMA comparisons are retrospective; live predictions require publication approval.',
                'feeding': 'Recorded feeding history is available; a feeding SARIMA model has not been validated.'}[key]}
    if period == 'monthly' and folds:
        views['regular']['metrics'] = score(folds)
    if period == 'weekly' and weekly.get('test_rows'):
        views['regular'].update(metrics=weekly['metrics'], status=weekly['status'],
            weekly_model_id=weekly['model_id'])
    if period != 'monthly':
        views['regular']['status_message'] = (
            'Yearly recorded sales and complete sums of monthly rolling predictions are shown; this is not a year-ahead forecast.'
            if period == 'yearly' else
            'Weekly SARIMA comparisons are retrospective research; no live weekly prediction is approved.'
            if weekly.get('test_rows') else
            'Weekly recorded sales are available. Run offline weekly SARIMA research for a historical comparison.')
    if period == 'monthly' and status == 'ready' and run is not None and issued is not None:
        views['regular']['forecast'] = [issued_point(run, issued)]
    date_field = 'date' if period == 'weekly' else 'month'
    default_range = ({'start_date': period_start(date.fromisoformat(folds[0][date_field]), period).isoformat(),
        'end_date': period_end(date.fromisoformat(folds[-1][date_field]), period).isoformat()} if folds else None)
    if period == 'yearly' and calendar:
        default_range = {'start_date': calendar[0].isoformat(),
                         'end_date': period_end(calendar[-1], period).isoformat()}
    provisional = provisional_report(period)
    for key, view in views.items():
        projection = provisional.get('views', {}).get(key, {})
        view['provisional_forecast'] = projection.get('points', [])
        view['provisional_status'] = projection.get('status', provisional['status'])
        view['provisional_metadata'] = {k: v for k, v in projection.items() if k != 'points'}
        if view['provisional_forecast']:
            view['status_message'] = 'Rough SARIMA projections are available separately; accuracy and uncertainty are not production approved.'
    return {'scope': 'real', 'period': period, 'available_periods': list(PERIODS),
        'provisional_status': provisional['status'],
        'provisional_model_id': provisional.get('model_id'),
        'provisional_source_revisions': provisional.get('source_revisions'),
        'provisional_targets': provisional.get('targets', []),
        'validated_forecast_periods': ['monthly'],
        'available_views': ['combined', 'regular', 'feeding'],
        'default_view': 'combined', 'source_revision': snapshot.revision,
        'as_of_exclusive': snapshot.as_of, 'views': views,
        'default_range': default_range,
        'warnings': ['Combined recorded revenue is regular plus feeding invoices; unclassified sales are excluded.',
            'A missing invoice period is not verified zero demand. Null graph values must remain gaps.',
            'Historical predictions and their WAPE are research evidence, separate from approved live forecasts.',
            'Dummy sales are excluded. Feeding predictions are never substituted with zero.',
            'Weekly buckets run Monday through Sunday and can span calendar years.',
            'Yearly prediction sums use twelve different monthly origins, not a year-ahead forecast. Monthly WAPE is not weekly or yearly accuracy.']}
