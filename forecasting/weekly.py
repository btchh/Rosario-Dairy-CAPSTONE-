"""Offline, actual-invoice weekly SARIMA research with past-only selection."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
import math
import warnings

import numpy as np
import statsmodels
from statsmodels.tsa.statespace.sarimax import SARIMAX
from threadpoolctl import threadpool_limits
from django.db import transaction
from django.utils import timezone

from .benchmarks import score
from .data import load_snapshot
from .learning import digest, month_end, shift_month
from .lifecycle import _commit_lock
from .models import WeeklyModelRun

VERSION = 'regular-weekly-sarima-v1'
MIN_TRAIN = 104
VALIDATION_WEEKS = 13
TEST_WEEKS = 13
MIN_WEEKS = MIN_TRAIN + VALIDATION_WEEKS + TEST_WEEKS
CONFIGS = {
    'weekly_sarima_010_100_52': {'order': (0, 1, 0), 'seasonal_order': (1, 0, 0, 52), 'trend': 'n'},
    'weekly_sarima_100_100_52': {'order': (1, 0, 0), 'seasonal_order': (1, 0, 0, 52), 'trend': 'c'},
    'weekly_sarima_011_100_52': {'order': (0, 1, 1), 'seasonal_order': (1, 0, 0, 52), 'trend': 'n'},
}
BASELINES = ('previous_week', 'trailing_4_mean', 'same_week_last_year')
LIMITATIONS = [
    'Retrospective research on recorded regular dairy revenue; no weekly production forecast is approved.',
    'Only invoice-bearing consecutive weeks are used. Missing weeks are not silently filled with zero.',
    'Invoice presence is not verified ledger coverage. Coverage review and prospective evaluation are still required.',
    'The annual seasonal period of 52 weeks approximates a calendar year; ISO week 53 is not silently merged.',
    'The 15% target belongs to monthly regular sales; it is not an established weekly accuracy guarantee.',
    'Historical data has already been inspected. This is not untouched acceptance evidence.',
    'Feeding, unclassified and dummy transactions are excluded. Error bands are empirical, not calibrated probability intervals.',
]


def week_start(day):
    return day-timedelta(days=day.weekday())


def weekly_snapshot(as_of=None):
    day = as_of or timezone.localdate()
    if not isinstance(day, date) or isinstance(day, datetime) or day > timezone.localdate():
        raise ValueError('as_of must be a business date no later than today.')
    return load_snapshot(week_start(day))


def weekly_rows(snapshot):
    """Full Monday–Sunday buckets inside the latest observed monthly span.

    Restrict both edges to avoid using partial source-month boundary weeks.
    Missing invoice months/weeks split history. A source gap is not a true zero.
    """
    totals = defaultdict(lambda: Decimal('0'))
    months = set()
    for sale in snapshot.sales:
        if sale.segment == 'regular':
            months.add(sale.day.replace(day=1))
            totals[week_start(sale.day)] += sale.revenue
    contiguous = []
    for month in sorted(months):
        if contiguous and month != shift_month(contiguous[-1]):
            contiguous = []
        contiguous.append(month)
    if not contiguous:
        return []
    first = week_start(contiguous[0])
    if first < contiguous[0]:
        first += timedelta(days=7)
    end = min(month_end(contiguous[-1]), snapshot.as_of-timedelta(days=1))
    rows = []
    while first+timedelta(days=6) <= end:
        if first in totals:
            rows.append({'date': first.isoformat(), 'revenue': float(totals[first])})
        else:
            rows = []
        first += timedelta(days=7)
    return rows


def _model(values, spec):
    return SARIMAX(values, order=tuple(spec['order']), seasonal_order=tuple(spec['seasonal_order']),
                   trend=spec['trend'], enforce_stationarity=True, enforce_invertibility=True)


def fit_weekly(name, rows):
    values = np.array([r['revenue'] for r in rows], dtype=float)
    if name not in CONFIGS or len(values) < MIN_TRAIN or not np.all(np.isfinite(values)) or np.any(values < 0):
        raise ValueError('Weekly SARIMA requires a known definition and 104 finite nonnegative earlier weeks.')
    if any(date.fromisoformat(r['date']).weekday() != 0 for r in rows):
        raise ValueError('Weekly observations must start on Monday.')
    for a, b in zip(rows, rows[1:]):
        if date.fromisoformat(a['date'])+timedelta(days=7) != date.fromisoformat(b['date']):
            raise ValueError('Weekly training history contains a gap.')
    scale = max(float(values.mean()), 1.0)
    normalized = (values/scale).tolist()
    spec = CONFIGS[name]
    attempts = []
    with threadpool_limits(limits=1), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        model = _model(normalized, spec)
        fitted = None
        for optimizer in ('lbfgs', 'powell'):
            fitted = model.fit(start_params=None if fitted is None else fitted.params,
                method=optimizer, maxiter=150, disp=False, cov_type='none')
            converged = bool(fitted.mle_retvals.get('converged', False))
            attempts.append({'optimizer': optimizer, 'converged': converged})
            if converged and np.all(np.isfinite(fitted.params)) and math.isfinite(fitted.llf):
                break
        else:
            raise ValueError('Weekly SARIMA did not converge to finite parameters.')
        prediction = _currency(float(fitted.forecast(1)[0])*scale)
    state = {'name': name, 'family': 'SARIMA', 'order': list(spec['order']),
        'seasonal_order': list(spec['seasonal_order']), 'trend': spec['trend'],
        'statsmodels_version': statsmodels.__version__, 'normalized_history': normalized,
        'scale': scale, 'params': fitted.params.tolist(), 'converged': True,
        'training_weeks': len(rows), 'trained_through': rows[-1]['date'],
        'target_week': (date.fromisoformat(rows[-1]['date'])+timedelta(days=7)).isoformat(),
        'optimization_attempts': attempts, 'fit_warnings': sorted({str(w.message) for w in caught})}
    digest(state)
    return state, prediction


def _currency(value):
    if not math.isfinite(value) or value >= 1e18:
        raise ValueError('Weekly prediction is outside supported currency precision.')
    return round(max(0, value), 2)


def predict_weekly(state):
    if not valid_weekly_artifact(state):
        raise ValueError('Invalid weekly SARIMA artifact.')
    with threadpool_limits(limits=1):
        filtered = _model(state['normalized_history'], state).filter(state['params'], cov_type='none')
        return _currency(float(filtered.forecast(1)[0])*state['scale'])


def valid_weekly_artifact(state):
    try:
        name = state['name']
        return (name in CONFIGS and state.get('family') == 'SARIMA' and state.get('converged') is True
            and state['training_weeks'] >= MIN_TRAIN
            and len(state['normalized_history']) == state['training_weeks']
            and math.isfinite(state['scale']) and state['scale'] > 0
            and bool(state['params']) and all(math.isfinite(v) for v in state['params'])
            and all(math.isfinite(v) and v >= 0 for v in state['normalized_history'])
            and all(state.get(k) == (list(v) if isinstance(v, tuple) else v) for k, v in CONFIGS[name].items())
            and date.fromisoformat(state['trained_through']).weekday() == 0
            and date.fromisoformat(state['target_week'])-date.fromisoformat(state['trained_through']) == timedelta(days=7))
    except (TypeError, ValueError, KeyError, OverflowError):
        return False


def _baseline(name, values):
    if name == 'previous_week':
        return values[-1]
    if name == 'trailing_4_mean':
        return sum(values[-4:])/4
    if name == 'same_week_last_year':
        return values[-52]
    raise ValueError('Unknown weekly baseline.')


def walk_weekly(name, rows, start, end):
    result = []
    for i in range(start, end):
        value = (fit_weekly(name, rows[:i])[1] if name in CONFIGS else
                 _currency(_baseline(name, [r['revenue'] for r in rows[:i]])))
        result.append({'date': rows[i]['date'], 'trained_through': rows[i-1]['date'],
                       'actual': rows[i]['revenue'], 'predicted': value})
    return result


def train_weekly(rows):
    if len(rows) < MIN_WEEKS:
        return {'status': 'insufficient_history', 'available_weeks': len(rows), 'required_weeks': MIN_WEEKS,
                'candidates': {}, 'limitations': LIMITATIONS}
    test_start = len(rows)-TEST_WEEKS
    validation_start = test_start-VALIDATION_WEEKS
    candidates = {}
    for name in (*CONFIGS, *BASELINES):
        candidate = candidates[name] = {'role': 'primary' if name in CONFIGS else 'comparison'}
        try:
            folds = walk_weekly(name, rows, validation_start, test_start)
            candidate.update(validation=score(folds), validation_rows=folds)
        except (ValueError, ArithmeticError, np.linalg.LinAlgError) as exc:
            candidate.update(status='failed', reason=str(exc))
    eligible = [n for n, c in candidates.items() if c.get('validation', {}).get('wape_percent') is not None]
    primary = [n for n in eligible if n in CONFIGS]
    baseline_names = [n for n in eligible if n in BASELINES]
    if not primary or not baseline_names:
        return {'status': 'no_valid_candidates', 'candidates': candidates, 'limitations': LIMITATIONS}
    selected = min(primary, key=lambda n: candidates[n]['validation']['wape_percent'])
    baseline = min(baseline_names, key=lambda n: candidates[n]['validation']['wape_percent'])
    # Winners are fixed using earlier validation before reading later targets.
    for name in eligible:
        try:
            folds = walk_weekly(name, rows, test_start, len(rows))
            candidates[name].update(test=score(folds), test_rows=folds)
        except (ValueError, ArithmeticError, np.linalg.LinAlgError) as exc:
            candidates[name]['test_failed'] = str(exc)
    if 'test' not in candidates[selected] or 'test' not in candidates[baseline]:
        return {'status': 'selected_model_failed', 'selected_candidate': selected,
                'candidates': candidates, 'limitations': LIMITATIONS}
    try:
        artifact, value = fit_weekly(selected, rows)
    except (ValueError, ArithmeticError, np.linalg.LinAlgError) as exc:
        return {'status': 'selected_model_failed', 'selected_candidate': selected,
                'reason': str(exc), 'candidates': candidates, 'limitations': LIMITATIONS}
    folds = candidates[selected]['test_rows']
    errors = sorted(abs(r['predicted']-r['actual']) for r in folds)
    width = errors[min(len(errors)-1, math.ceil(.9*(len(errors)+1))-1)]
    return {'status': 'research_candidate', 'engine_version': VERSION, 'model_family': 'SARIMA',
        'selected_candidate': selected, 'selected_baseline': baseline,
        'selection_basis': 'earlier_validation_weekly_wape',
        'training_weeks': len(rows), 'validation_start': rows[validation_start]['date'],
        'validation_end': rows[test_start-1]['date'], 'test_start': rows[test_start]['date'],
        'test_end': rows[-1]['date'], 'selected_test': candidates[selected]['test'],
        'baseline_test': candidates[baseline]['test'], 'artifact': artifact, 'artifact_sha256': digest(artifact),
        'next_week': {'date': artifact['target_week'], 'predicted_revenue': value,
            'lower_bound': round(max(0, value-width), 2), 'upper_bound': round(value+width, 2),
            'range_kind': 'historical_absolute_error_band', 'sample_count': len(folds)},
        'candidates': candidates, 'limitations': LIMITATIONS}


def refresh_weekly(as_of=None):
    snapshot = weekly_snapshot(as_of)
    existing = WeeklyModelRun.objects.filter(engine_version=VERSION, source_revision=snapshot.revision,
                                             cutoff=snapshot.as_of).first()
    if existing:
        return existing, False
    rows = weekly_rows(snapshot)
    signature = digest(rows)
    previous = WeeklyModelRun.objects.filter(engine_version=VERSION).first()
    if previous and previous.findings.get('training_signature') == signature:
        findings = {**previous.findings, 'reused_from_model_id': previous.pk}
    else:
        findings = train_weekly(rows)
    findings['training_signature'] = signature
    with transaction.atomic():
        _commit_lock()
        current = weekly_snapshot(as_of)
        if current.revision != snapshot.revision or current.as_of != snapshot.as_of:
            raise ValueError('Actual weekly data changed during fitting; retry.')
        return WeeklyModelRun.objects.get_or_create(engine_version=VERSION,
            source_revision=snapshot.revision, cutoff=snapshot.as_of,
            defaults={'trained_through': date.fromisoformat(rows[-1]['date'])+timedelta(days=6) if rows else None,
                      'status': findings['status'], 'findings': findings})


def weekly_report(snapshot=None):
    snapshot = snapshot or weekly_snapshot()
    training_rows = weekly_rows(snapshot)
    run = WeeklyModelRun.objects.filter(engine_version=VERSION, source_revision=snapshot.revision,
                                        cutoff=snapshot.as_of).first()
    base = {'status': 'refresh_required', 'engine_version': VERSION, 'scope': 'real', 'period': 'weekly',
        'source_revision': snapshot.revision, 'as_of_exclusive': snapshot.as_of, 'is_live': False,
        'available_weeks': len(training_rows), 'required_weeks': MIN_WEEKS, 'production_ready': False,
        'forecast': [], 'test_rows': [], 'metrics': None, 'research_forecast': None,
        'limitations': LIMITATIONS}
    if run is None:
        return base
    f = run.findings
    base.update(model_id=run.pk, model_status=run.status, status=run.status, trained_through=run.trained_through,
        selected_candidate=f.get('selected_candidate'), selected_baseline=f.get('selected_baseline'),
        candidates={n: {k: v for k, v in c.items() if k not in ('validation_rows', 'test_rows')}
                    for n, c in f.get('candidates', {}).items()},
        evaluation={k: f.get(k) for k in ('selection_basis', 'training_weeks', 'validation_start',
                    'validation_end', 'test_start', 'test_end')})
    if run.status != 'research_candidate':
        return base
    artifact = f.get('artifact', {})
    if (not valid_weekly_artifact(artifact) or digest(artifact) != f.get('artifact_sha256')
            or artifact.get('name') != f.get('selected_candidate')):
        base['status'] = 'invalid_model'
        return base
    rows = f.get('candidates', {}).get(f.get('selected_candidate'), {}).get('test_rows', [])
    actual = {r['date']: r['revenue'] for r in training_rows}
    seen = set()
    for row in rows:
        try:
            target = date.fromisoformat(row['date'])
            if (target.weekday() != 0 or row['date'] in seen
                    or date.fromisoformat(row['trained_through'])+timedelta(days=7) != target
                    or row['date'] not in actual or not math.isfinite(row['actual'])
                    or abs(actual[row['date']]-row['actual']) > .005
                    or not math.isfinite(row['predicted']) or row['predicted'] < 0):
                raise ValueError('Inconsistent weekly comparison')
            seen.add(row['date'])
        except (ValueError, TypeError, KeyError, OverflowError):
            base['status'] = 'invalid_comparison'
            return base
    if (len(rows) != TEST_WEEKS or any(date.fromisoformat(b['date'])-date.fromisoformat(a['date'])
            != timedelta(days=7) for a, b in zip(rows, rows[1:]))):
        base['status'] = 'invalid_comparison'
        return base
    base.update(status='stale_data' if run.trained_through != snapshot.as_of-timedelta(days=1) else 'research_only',
                metrics=score(rows), baseline_metrics=f.get('baseline_test'), test_rows=rows,
                research_forecast=f.get('next_week'), research_forecast_is_live=False)
    baseline_wape = (base['baseline_metrics'] or {}).get('wape_percent')
    model_wape = base['metrics']['wape_percent']
    base['beats_baseline'] = (model_wape < baseline_wape
        if model_wape is not None and baseline_wape is not None else None)
    return base
