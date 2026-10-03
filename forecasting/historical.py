"""Unvalidated current-period estimates from complete historical workbooks."""
from calendar import monthrange
from datetime import date, timedelta
from statistics import median


def period_projection(daily, period, today):
    """Return a current-period median and observed range, or None without coverage."""
    if period not in ('weekly', 'monthly', 'yearly'):
        raise ValueError('Invalid forecast period')
    if period == 'weekly':
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    elif period == 'monthly':
        start = today.replace(day=1)
        end = today.replace(day=monthrange(today.year, today.month)[1])
    else:
        start, end = date(today.year, 1, 1), date(today.year, 12, 31)

    samples = []
    for year in range(2023, min(2025, today.year - 1) + 1):
        # The annual workbooks cover 2023–2025. Require recorded activity in
        # both January and December before treating missing dates as zero.
        if not (any(day.year == year and day.month == 1 for day in daily)
                and any(day.year == year and day.month == 12 for day in daily)):
            continue
        if period == 'weekly':
            try:
                historical_start = date.fromisocalendar(year, today.isocalendar().week, 1)
            except ValueError:
                continue
            historical_end = historical_start + timedelta(days=6)
        elif period == 'monthly':
            historical_start = date(year, today.month, 1)
            historical_end = date(year, today.month, monthrange(year, today.month)[1])
        else:
            historical_start, historical_end = date(year, 1, 1), date(year, 12, 31)
        value = sum(float(daily.get(historical_start + timedelta(days=offset), 0))
                    for offset in range((historical_end - historical_start).days + 1))
        samples.append(round(value, 2))

    if len(samples) < 2:
        return None
    return {
        'date': start, 'end_date': end, 'trained_through': max(daily),
        'predicted_revenue': round(median(samples), 2),
        'lower_bound': min(samples), 'upper_bound': max(samples),
        'point_kind': 'historical_median', 'range_kind': 'observed_historical_min_max',
        'sample_count': len(samples),
    }
