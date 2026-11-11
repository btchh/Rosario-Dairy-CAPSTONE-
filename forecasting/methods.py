"""Operational forecasting catalog and implementation: SARIMA only."""
import math
import warnings

import numpy as np
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.statespace.sarimax import SARIMAX
from threadpoolctl import threadpool_limits


PERIOD_DEFAULTS = {'daily': 14, 'weekly': 8, 'monthly': 12}
SEASONS = {'daily': 7, 'weekly': 52, 'monthly': 12}
HORIZONS = {
    'short': {'daily': [7, 14, 28], 'weekly': [4, 8]},
    'medium': {'weekly': [13, 26], 'monthly': [6, 12]},
    'long': {'monthly': [24, 36]},
}


class ForecastConfigurationError(ValueError):
    """The requested method or configuration is not supported."""


_PATTERNS = ['stable', 'trending', 'seasonal', 'cyclical', 'intermittent', 'erratic', 'lumpy']
_SARIMA = {
    'id': 'sarima',
    'name': 'SARIMA',
    'description': 'Seasonal ARIMA(1,0,0)(1,0,0), using the aggregation seasonal period.',
    'category': 'time_series',
    'approach': 'quantitative',
    'minimum_training_periods': 24,
    'requires_complete_history': False,
    'supported_periods': list(SEASONS),
    'parameters': {},
    'supported_patterns': _PATTERNS,
    'implementation_status': 'implemented',
    'required_data': ('Finite, nonnegative recorded values on the original calendar; at least 60% observed coverage '
                     'and two seasonal cycles. Internal unknown gaps bracketed by observations are linearly '
                     'interpolated for model fitting; leading and trailing unknowns remain missing. '
                     'Seasonal reference: daily=7, weekly=52, monthly=12.'),
    'eligibility_conditions': ('Requirements apply to the earliest training fold; separate full-horizon '
                              'validation and test blocks must also be available.'),
}
METHODS = {'sarima': _SARIMA}


def normalize_parameters(method, supplied=None):
    if method not in METHODS:
        raise ForecastConfigurationError('This method is not operationally supported.')
    if supplied is None:
        return {}
    if not isinstance(supplied, dict) or supplied:
        raise ForecastConfigurationError('SARIMA does not accept configurable parameters.')
    return {}


def clean(values):
    result = []
    for value in values:
        if value is None:
            result.append(None)
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError('History must contain finite nonnegative recorded values or explicit unknowns.') from error
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError('History must contain finite nonnegative recorded values or explicit unknowns.')
        result.append(numeric)
    return result


def interpolate_internal_gaps(values):
    """Linearly fill unknown calendar periods only when observations bracket them.

    Explicit zeroes remain zero. Leading and trailing unknowns remain unknown,
    and interpolation never crosses beyond the supplied history window.
    """
    result = clean(values)
    observed = [index for index, value in enumerate(result) if value is not None]
    for left, right in zip(observed, observed[1:]):
        if right-left <= 1:
            continue
        start, end = result[left], result[right]
        step = (end-start)/(right-left)
        for index in range(left+1, right):
            result[index] = start + step*(index-left)
    return result


def _minimum_training(season):
    return max(24, 2 * season)


def eligibility(values, method, period, params=None):
    try:
        normalize_parameters(method, params)
        values = clean(values)
    except (ValueError, TypeError) as error:
        return str(error)
    if period not in SEASONS:
        return 'Unsupported aggregation period.'
    required = _minimum_training(SEASONS[period])
    known = sum(value is not None for value in values)
    if len(values) < required or known < required:
        return (f'Requires at least {required} observed training periods across two seasonal cycles '
                f'before the validation and test blocks; {known} are available.')
    if known < .6 * len(values):
        return 'Less than 60% of calendar training periods are observed.'
    return None


def decomposition(values, season):
    """Return descriptive STL history components; this is not a forecast method."""
    values = clean(values)
    if not isinstance(season, int) or isinstance(season, bool) or season < 2:
        raise ValueError('Unsupported seasonal reference.')
    if len(values) < 2 * season or any(value is None for value in values):
        raise ValueError('Descriptive STL requires two complete observed seasonal cycles.')
    fit = STL(np.asarray(values, dtype=float), period=season, robust=True).fit()
    return {'trend': fit.trend.tolist(), 'seasonal': fit.seasonal.tolist(), 'residual': fit.resid.tolist()}


def forecast(values, method, horizon, season, parameters=None):
    values = clean(values)
    normalize_parameters(method, parameters)
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1 or horizon > 5000:
        raise ValueError('Unsupported projection distance.')
    if not isinstance(season, int) or isinstance(season, bool) or season < 2:
        raise ValueError('Unsupported seasonal reference.')
    known = [value for value in values if value is not None]
    required = _minimum_training(season)
    if len(values) < required or len(known) < required:
        raise ValueError(f'SARIMA requires at least {required} observed periods across two seasonal cycles.')
    if len(known) < .6 * len(values):
        raise ValueError('SARIMA requires at least 60% observed calendar coverage.')

    # Fill only gaps that have observations on both sides inside this training
    # window. The original observed-count and coverage requirements above still
    # apply, so interpolation cannot manufacture seasonal history.
    model_values = interpolate_internal_gaps(values)

    # Scaling keeps the numerical optimizer stable across units and price scales.
    scale = max(float(np.mean(known)), 1.0)
    observed = np.asarray([np.nan if value is None else value / scale for value in model_values], dtype=float)
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter('ignore')
        model = SARIMAX(observed, order=(1, 0, 0), seasonal_order=(1, 0, 0, season), trend='c')
        fitted = model.fit(disp=False, maxiter=75)
    if not fitted.mle_retvals.get('converged'):
        raise ValueError('SARIMA optimization did not converge.')
    result = np.asarray(fitted.forecast(horizon), dtype=float) * scale
    if result.shape != (horizon,) or not np.isfinite(result).all():
        raise ValueError('SARIMA produced nonfinite predictions.')
    return [max(0.0, float(value)) for value in result]
