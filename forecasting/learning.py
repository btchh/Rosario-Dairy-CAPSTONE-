"""Past-only monthly regular-dairy modelling with portable fitted state.

SARIMA definitions are fixed before evaluation. Select among SARIMA on six
earlier validation months, then refit at twelve later origins. Other families
are comparison benchmarks. Test scores never choose the primary model.
"""
import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
import math
import warnings

import numpy as np
from statsmodels.tsa.holtwinters import Holt, SimpleExpSmoothing
from statsmodels.tools.sm_exceptions import ConvergenceWarning

from .benchmarks import BENCHMARKS, predict as baseline_predict, score
from .seasonal import SARIMA_CONFIGS, fit_sarima, predict_sarima

ENGINE_VERSION = 'regular-monthly-sarima-v2'
COMPARATORS = (*BENCHMARKS, 'exponential_level', 'damped_trend', 'ridge_ar_10', 'ridge_ar_100')
CANDIDATES = (*SARIMA_CONFIGS, *COMPARATORS)
MIN_MONTHS = 36
VALIDATION_MONTHS = 6
TEST_MONTHS = 12
TARGET_PERCENT = 15.0


def month_start(day):
    return day.replace(day=1)


def shift_month(month, count=1):
    index = month.year * 12 + month.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


def month_end(month):
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def digest(value):
    # PostgreSQL JSONB normalizes -0.0 to 0.0. Canonicalize that harmless
    # numeric representation before hashing, including nested fitted arrays.
    def canonical(item):
        if isinstance(item, float) and item == 0:
            return 0.0
        if isinstance(item, dict):
            return {key: canonical(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [canonical(child) for child in item]
        return item
    return sha256(json.dumps(canonical(value), sort_keys=True, allow_nan=False).encode()).hexdigest()


def month_revision(snapshot, month):
    # Include unresolved/feeding records: reclassification changes coverage.
    return digest([(s.id, s.day.isoformat(), s.customer_key, s.segment, str(s.revenue))
                   for s in snapshot.sales if month_start(s.day) == month])


def monthly_rows(snapshot, confirmed_zero_months=()):
    months = defaultdict(lambda: Decimal('0'))
    for sale in snapshot.sales:
        month = month_start(sale.day)
        if sale.segment == 'regular' and month < month_start(snapshot.as_of):
            months[month] += sale.revenue
    for month in confirmed_zero_months:
        if month < month_start(snapshot.as_of):
            months.setdefault(month, Decimal('0'))
    # Missing months split history, rather than masquerading as zero sales.
    contiguous = []
    for month, total in sorted(months.items()):
        if contiguous and month != shift_month(date.fromisoformat(contiguous[-1]['month'])):
            contiguous = []
        contiguous.append({'month': month.isoformat(), 'revenue': float(total)})
    return contiguous


def _features(history, target):
    phase = 2 * math.pi * target.month / 12
    return [history[-1], float(np.mean(history[-3:])), history[-1] - history[-2],
            math.sin(phase), math.cos(phase), calendar.monthrange(target.year, target.month)[1]]


def fit_model(name, rows):
    values = np.array([r['revenue'] for r in rows], dtype=float)
    if len(values) < 12 or not np.all(np.isfinite(values)) or np.any(values < 0):
        raise ValueError('At least twelve finite nonnegative monthly totals are required.')
    target = shift_month(date.fromisoformat(rows[-1]['month']))
    state = {'name': name, 'trained_through': rows[-1]['month'], 'target_month': target.isoformat(),
             'training_months': len(rows)}
    if name in SARIMA_CONFIGS:
        state.update(fit_sarima(name, values))
    elif name in BENCHMARKS:
        state['value'] = float(baseline_predict(values.tolist(), name))
    elif name in ('exponential_level', 'damped_trend'):
        # Normalize for optimizer conditioning; restore currency in fitted state.
        scale = max(float(values.mean()), 1.0)
        with warnings.catch_warnings():
            warnings.simplefilter('error', ConvergenceWarning)
            # Perfect constant fits have log(0) in unused AIC/BIC diagnostics.
            # Fitted state and predictions still undergo finite-value checks.
            warnings.filterwarnings('ignore', message='divide by zero encountered in log',
                                    category=RuntimeWarning, module=r'statsmodels\.tsa\.holtwinters\.model')
            model = (SimpleExpSmoothing(values / scale, initialization_method='estimated')
                     if name == 'exponential_level' else
                     Holt(values / scale, damped_trend=True, initialization_method='estimated'))
            fitted = model.fit(optimized=True, use_brute=False)
        state.update(level=float(fitted.level[-1] * scale),
                     smoothing_level=float(fitted.params['smoothing_level']))
        if name == 'damped_trend':
            state.update(trend=float(fitted.trend[-1] * scale),
                         smoothing_trend=float(fitted.params['smoothing_trend']),
                         damping=float(fitted.params['damping_trend']))
    elif name in ('ridge_ar_10', 'ridge_ar_100'):
        x = np.array([_features(values[:i], date.fromisoformat(rows[i]['month']))
                      for i in range(3, len(rows))])
        y = values[3:]
        center, spread = x.mean(axis=0), x.std(axis=0)
        spread[spread < 1e-8] = 1.0
        scaled = (x - center) / spread
        penalty = float(name.rsplit('_', 1)[1])
        intercept = float(y.mean())
        coefficients = np.linalg.solve(scaled.T @ scaled + penalty * np.eye(x.shape[1]),
                                        scaled.T @ (y - intercept))
        state.update(coefficients=coefficients.tolist(), center=center.tolist(), spread=spread.tolist(),
                     intercept=intercept, last_values=values[-3:].tolist(),
                     feature_names=['lag_1', 'trailing_3_mean', 'last_change', 'month_sin', 'month_cos', 'month_days'],
                     penalty=penalty)
    else:
        raise ValueError(f'Unknown candidate: {name}')
    # Reject nonportable/NaN state before it can reach the database.
    digest(state)
    return state


def predict_model(state):
    name = state['name']
    if name in SARIMA_CONFIGS:
        value = predict_sarima(state)
    elif name in BENCHMARKS:
        value = state['value']
    elif name == 'exponential_level':
        value = state['level']
    elif name == 'damped_trend':
        value = state['level'] + state['damping'] * state['trend']
    elif name in ('ridge_ar_10', 'ridge_ar_100'):
        features = np.array(_features(state['last_values'], date.fromisoformat(state['target_month'])))
        value = state['intercept'] + float(((features - state['center']) / state['spread']) @ state['coefficients'])
    else:
        raise ValueError('Invalid fitted candidate')
    if not math.isfinite(value):
        raise ValueError('Nonfinite model prediction')
    if value >= 1e18:
        raise ValueError('Prediction exceeds the supported currency precision.')
    return round(max(0, value), 2)


def walk_forward(name, rows, start, end):
    result = []
    for index in range(start, end):
        fitted = fit_model(name, rows[:index])
        result.append({'month': rows[index]['month'], 'trained_through': rows[index - 1]['month'],
                       'actual': rows[index]['revenue'], 'predicted': predict_model(fitted)})
        if name in SARIMA_CONFIGS:
            result[-1]['fit_diagnostics'] = {key: fitted[key] for key in
                ('converged', 'optimization_attempts', 'fit_warnings')}
    return result


def train(rows):
    if len(rows) < MIN_MONTHS:
        return {'status': 'insufficient_history', 'required_months': MIN_MONTHS,
                'available_months': len(rows), 'candidates': {}}
    # Accept only already-contiguous, completed observations from the caller.
    for previous, current in zip(rows, rows[1:]):
        if shift_month(date.fromisoformat(previous['month'])).isoformat() != current['month']:
            raise ValueError('Training history contains missing months.')
    validation_start = len(rows) - TEST_MONTHS - VALIDATION_MONTHS
    test_start = len(rows) - TEST_MONTHS
    candidates = {}
    for name in CANDIDATES:
        try:
            folds = walk_forward(name, rows, validation_start, test_start)
            candidates[name] = {'role': 'primary' if name in SARIMA_CONFIGS else 'comparison',
                                'validation': score(folds), 'validation_rows': folds}
        except (ValueError, ArithmeticError, ConvergenceWarning, np.linalg.LinAlgError) as exc:
            candidates[name] = {'role': 'primary' if name in SARIMA_CONFIGS else 'comparison',
                                'status': 'failed', 'reason': str(exc)}
    eligible = [name for name, c in candidates.items()
                if c.get('validation', {}).get('wape_percent') is not None]
    primary = [name for name in eligible if name in SARIMA_CONFIGS]
    if not primary:
        return {'status': 'no_valid_candidates', 'candidates': candidates}
    # The order breaks equal-score ties toward simpler candidates. Test values
    # have not been used in any fitting/selection at this point.
    selected = min(primary, key=lambda n: (candidates[n]['validation']['wape_percent'], CANDIDATES.index(n)))
    baselines = [n for n in eligible if n in BENCHMARKS]
    if not baselines:
        return {'status': 'no_valid_baseline', 'selected_candidate': selected, 'candidates': candidates}
    baseline = min(baselines,
                   key=lambda n: (candidates[n]['validation']['wape_percent'], CANDIDATES.index(n)))
    for name in eligible:
        try:
            folds = walk_forward(name, rows, test_start, len(rows))
            candidates[name].update(test=score(folds), test_rows=folds)
        except (ValueError, ArithmeticError, ConvergenceWarning, np.linalg.LinAlgError) as exc:
            candidates[name].update(test_failed=str(exc))
    # A selected candidate that fails later fitting is rejected. Do not pick
    # a different winner by inspecting the test results.
    if 'test' not in candidates[selected]:
        return {'status': 'selected_model_failed', 'selected_candidate': selected, 'candidates': candidates}
    try:
        fitted = fit_model(selected, rows)
        value = predict_model(fitted)
    except (ValueError, ArithmeticError, ConvergenceWarning, np.linalg.LinAlgError) as exc:
        return {'status': 'selected_model_failed', 'selected_candidate': selected,
                'failure_phase': 'final_refit', 'reason': str(exc), 'candidates': candidates}
    selected_test = candidates[selected]['test']
    errors = sorted(abs(r['predicted'] - r['actual']) for r in candidates[selected]['test_rows'])
    width = errors[min(len(errors) - 1, math.ceil(.9 * (len(errors) + 1)) - 1)]
    baseline_test = candidates[baseline].get('test', {})
    under_target = (selected_test['wape_percent'] is not None and selected_test['wape_percent'] < TARGET_PERCENT)
    no_worse = (baseline_test.get('wape_percent') is not None and
                selected_test['wape_percent'] <= baseline_test['wape_percent'])
    return {
        'status': 'research_candidate', 'engine_version': ENGINE_VERSION,
        'model_family': 'SARIMA', 'seasonal_period_months': 12,
        'selection_basis': 'sarima_only_earlier_validation_wape', 'selected_candidate': selected,
        'selected_baseline': baseline, 'validation_start': rows[validation_start]['month'],
        'validation_end': rows[test_start - 1]['month'], 'test_start': rows[test_start]['month'],
        'test_end': rows[-1]['month'], 'training_start': rows[0]['month'],
        'training_end': rows[-1]['month'], 'training_months': len(rows),
        'selected_test': selected_test, 'baseline_test': baseline_test,
        'retrospective_gate': {'below_15_percent': under_target, 'no_worse_than_baseline': no_worse},
        'artifact': fitted, 'artifact_sha256': digest(fitted),
        'next_month': {'date': fitted['target_month'], 'predicted_revenue': value,
                       'lower_bound': round(max(0, value - width), 2), 'upper_bound': round(value + width, 2),
                       'range_kind': 'historical_absolute_error_band', 'sample_count': len(errors)},
        'candidates': candidates,
        'limitations': [
            f'This fit has {len(rows)} months; the earliest validation fit has {validation_start} months. Short histories provide limited evidence for annual seasonality.',
            'SARIMA configurations alone compete for the primary forecast; other methods are comparison benchmarks, never silent fallbacks.',
            'The 2025 history has already been inspected; historical scores are retrospective research, not untouched acceptance evidence.',
            'Only one-month-ahead regular dairy revenue is evaluated; feeding, product quantities and longer horizons are not forecast.',
            'The displayed range summarizes historical absolute errors and is not a calibrated probability interval.',
            'Coverage attestations and prospective issued-forecast scores are required before publication.',
        ],
    }
