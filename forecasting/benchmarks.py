"""Chronological research benchmarks; never production forecasts."""
from statistics import mean, median

BENCHMARKS = ('previous_month', 'trailing_3_mean', 'trailing_6_mean', 'trailing_3_median', 'same_month_last_year')


def score(rows):
    total = sum(abs(row['actual']) for row in rows)
    error = sum(abs(row['predicted'] - row['actual']) for row in rows)
    return {'periods': len(rows), 'wape_percent': round(100 * error / total, 4) if total else None,
            'mae': round(error / len(rows), 2) if rows else None,
            'bias': round(sum(row['predicted'] - row['actual'] for row in rows) / len(rows), 2) if rows else None}


def predict(history, name):
    if name == 'previous_month':return history[-1]
    if name == 'trailing_3_mean':return mean(history[-3:])
    if name == 'trailing_6_mean':return mean(history[-6:])
    if name == 'trailing_3_median':return median(history[-3:])
    if name == 'same_month_last_year':return history[-12]
    raise ValueError('Unknown research benchmark')


def evaluate(months):
    """Latest contiguous calendar history, without inventing missing months.

    Reserve twelve later months as test; select a benchmark on the preceding
    six months. Each prediction may use completed earlier actual months only.
    This is retrospective research on recorded totals, not an accuracy claim.
    """
    history = []
    for row in months:
        year, month = map(int, row['month'].split('-'))
        ordinal = year * 12 + month
        if history and ordinal != history[-1][0] + 1:history = []
        history.append((ordinal, row))
    if len(history) < 30:
        return {'status': 'insufficient_history', 'required_contiguous_months': 30,
                'available_contiguous_months': len(history), 'benchmarks': {}}
    rows = [item[1] for item in history]
    validation_start, test_start = len(rows) - 18, len(rows) - 12
    values = [float(row['regular_revenue']) for row in rows]
    candidates = {}
    for name in BENCHMARKS:
        def walk(start, end):
            return [{'month': rows[i]['month'], 'trained_through': rows[i-1]['month'],
                     'actual': values[i], 'predicted': round(predict(values[:i], name), 2)} for i in range(start, end)]
        validation = walk(validation_start, test_start)
        test = walk(test_start, len(rows))
        candidates[name] = {'validation': score(validation), 'test': score(test),
                            'validation_rows': validation, 'test_rows': test}
    eligible = [name for name in BENCHMARKS if candidates[name]['validation']['wape_percent'] is not None]
    selected = min(eligible, key=lambda name: (candidates[name]['validation']['wape_percent'], name)) if eligible else None
    return {'status': 'research_only', 'validation_start': rows[validation_start]['month'],
            'validation_end': rows[test_start-1]['month'], 'test_start': rows[test_start]['month'],
            'test_end': rows[-1]['month'], 'selection_basis': 'validation_wape_only',
            'selected_benchmark': selected,
            'selected_test': candidates[selected]['test'] if selected else None,
            'benchmarks': candidates}
