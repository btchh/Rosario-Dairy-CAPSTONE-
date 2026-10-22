"""Bounded monthly SARIMA search and portable, non-pickle fitted state."""
import math
import warnings

import numpy as np
import statsmodels
from statsmodels.tsa.statespace.sarimax import SARIMAX
from threadpoolctl import threadpool_limits

# Fixed before evaluation. Each configuration has an annual seasonal AR term.
# Keep the search small: initial validation folds contain only 18 months.
SARIMA_CONFIGS = {
    'sarima_000_100_12': {'order': (0, 0, 0), 'seasonal_order': (1, 0, 0, 12), 'trend': 'c'},
    'sarima_100_100_12': {'order': (1, 0, 0), 'seasonal_order': (1, 0, 0, 12), 'trend': 'c'},
    'sarima_010_100_12': {'order': (0, 1, 0), 'seasonal_order': (1, 0, 0, 12), 'trend': 'n'},
    'sarima_110_100_12': {'order': (1, 1, 0), 'seasonal_order': (1, 0, 0, 12), 'trend': 'n'},
    'sarima_011_100_12': {'order': (0, 1, 1), 'seasonal_order': (1, 0, 0, 12), 'trend': 'n'},
}
MIN_SARIMA_TRAINING_MONTHS = 18


def _model(history, spec):
    return SARIMAX(history, order=tuple(spec['order']),
                   seasonal_order=tuple(spec['seasonal_order']), trend=spec['trend'],
                   enforce_stationarity=True, enforce_invertibility=True)


def fit_sarima(name, values):
    if len(values) < MIN_SARIMA_TRAINING_MONTHS:
        raise ValueError('SARIMA needs at least eighteen earlier monthly observations.')
    spec = SARIMA_CONFIGS[name]
    scale = max(float(np.mean(values)), 1.0)
    history = (values / scale).tolist()
    attempts = []
    # Limit BLAS threads so small state-space fits do not oversubscribe workers.
    with threadpool_limits(limits=1), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        model = _model(history, spec)
        fitted = None
        for optimizer in ('lbfgs', 'powell'):
            fitted = model.fit(start_params=None if fitted is None else fitted.params,
                               method=optimizer, maxiter=150, disp=False, cov_type='none')
            converged = bool(fitted.mle_retvals.get('converged', False))
            attempts.append({'optimizer': optimizer, 'converged': converged})
            if converged and np.all(np.isfinite(fitted.params)) and math.isfinite(fitted.llf):
                break
        else:
            raise ValueError('SARIMA optimization failed to converge to finite parameters.')
        next_value = float(fitted.forecast(1)[0] * scale)
    if not math.isfinite(next_value):
        raise ValueError('SARIMA produced a nonfinite forecast.')
    return {
        'family': 'SARIMA', 'implementation': 'statsmodels.SARIMAX',
        'statsmodels_version': statsmodels.__version__,
        'order': list(spec['order']), 'seasonal_order': list(spec['seasonal_order']),
        'trend': spec['trend'], 'scale': scale, 'normalized_history': history,
        'params': fitted.params.tolist(), 'param_names': list(fitted.param_names),
        'converged': True, 'optimization_attempts': attempts,
        'fit_warnings': sorted({str(w.message) for w in caught}),
        'log_likelihood': float(fitted.llf),
        'fitted_next_value': next_value,
    }


def predict_sarima(state):
    """Rebuild the Kalman filter with saved parameters; never optimize on reads."""
    if not is_sarima_artifact(state):
        raise ValueError('Invalid fitted SARIMA artifact.')
    with threadpool_limits(limits=1):
        filtered = _model(state['normalized_history'], state).filter(state['params'], cov_type='none')
        return float(filtered.forecast(1)[0] * state['scale'])


def is_sarima_artifact(state):
    if not isinstance(state, dict) or state.get('name') not in SARIMA_CONFIGS:
        return False
    spec = SARIMA_CONFIGS[state['name']]
    return (state.get('family') == 'SARIMA' and state.get('converged') is True and
            state.get('order') == list(spec['order']) and
            state.get('seasonal_order') == list(spec['seasonal_order']) and
            state.get('trend') == spec['trend'] and
            len(state.get('normalized_history', [])) == state.get('training_months') and
            state.get('training_months', 0) >= MIN_SARIMA_TRAINING_MONTHS and
            bool(state.get('params')) and
            len(state['params']) == len(state.get('param_names', [])))
