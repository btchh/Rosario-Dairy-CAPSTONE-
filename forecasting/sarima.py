"""Bounded SARIMA search; candidate construction sees training data only."""
from dataclasses import asdict, dataclass
import warnings

import numpy as np
from threadpoolctl import threadpool_limits


@dataclass(frozen=True)
class Specification:
    order: tuple
    seasonal_order: tuple = (0, 0, 0, 0)
    transform: str = 'level'
    window: int | None = None

    @property
    def name(self):
        family = 'sarima' if any(self.seasonal_order[:3]) else 'arima'
        return f'{family}_{self.order}_{self.seasonal_order}_{self.transform}_{self.window or "all"}'

    def values(self):
        return {'name': self.name, **asdict(self)}


def diagnostics(history, period):
    from statsmodels.tsa.stattools import acf
    values = np.log1p(np.asarray(history, dtype=float))
    x = np.arange(len(values))
    slope, intercept = np.polyfit(x, values, 1)
    residual = values - (intercept + slope * x)
    seasonal = (3, 6, 12) if period == 'monthly' else (4, 13, 26, 52)
    correlations = acf(residual, nlags=min(max(seasonal), len(values) - 1), fft=False) if residual.std() > 1e-8 else np.zeros(len(values))
    assessed = [{'periods': lag, 'complete_cycles': len(values) // lag,
                 'detrended_log_acf': round(float(correlations[lag]), 4) if lag < len(correlations) else None,
                 'eligible': len(values) >= 2 * lag + 4} for lag in seasonal]
    return {'training_observations': len(values), 'zero_periods': int((history == 0).sum()),
            'log_trend_per_period': round(float(slope), 6), 'seasonal_hypotheses': assessed,
            'rule': 'Calendar seasonal hypotheses require two complete cycles plus four observations; validation determines whether they help.'}


def candidates(history, period):
    evidence = diagnostics(history, period)
    orders = ((0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1),
              (2, 0, 0), (0, 1, 0), (0, 1, 1), (1, 1, 0), (1, 1, 1))
    result = [Specification(order, transform=transform) for transform in ('level', 'log') for order in orders]
    recent = 12 if period == 'monthly' else 52
    if len(history) > recent:
        result.extend(Specification(order, transform=transform, window=recent)
                      for transform in ('level', 'log') for order in ((1, 0, 0), (0, 1, 1)))
    for item in evidence['seasonal_hypotheses']:
        if not item['eligible']:
            continue
        lag = item['periods']
        for transform in ('level', 'log'):
            for order, seasonal in (((1, 0, 0), (1, 0, 0, lag)),
                                    ((0, 0, 1), (0, 0, 1, lag)),
                                    ((0, 1, 1), (0, 1, 1, lag))):
                result.append(Specification(order, seasonal, transform))
    return tuple(result), evidence


def forecast(history, spec, steps=1):
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    if steps < 1 or history.empty:
        raise ValueError('A nonempty history and positive horizon are required')
    training = history.iloc[-spec.window:] if spec.window else history
    if len(training) < 8 or not np.isfinite(training).all() or (training < 0).any():
        raise ValueError('Invalid SARIMA training observations')
    # A new program has genuine zero historical revenue before its first sale.
    if float(training.max()) == 0:
        return tuple(np.zeros(steps) for _ in range(3))
    if float(training.std()) < 1e-8:
        return tuple(np.full(steps, float(training.iloc[-1])) for _ in range(3))
    scale = max(float(training.mean()), 1.0)
    encoded = training / scale
    if spec.transform == 'log':
        encoded = np.log1p(encoded)
    elif spec.transform != 'level':
        raise ValueError('Unknown revenue transformation')
    differenced = spec.order[1] + spec.seasonal_order[1] > 0
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter('ignore')
        model = SARIMAX(encoded, order=spec.order, seasonal_order=spec.seasonal_order,
                        trend='n' if differenced else 'c',
                        enforce_stationarity=True, enforce_invertibility=True)
        fitted = model.fit(disp=False, maxiter=200, cov_type='none')
        if not fitted.mle_retvals.get('converged', False):
            fitted = model.fit(start_params=fitted.params, method='powell',
                               disp=False, maxiter=60, maxfun=1500, cov_type='none')
        if not fitted.mle_retvals.get('converged', False):
            raise ValueError('SARIMA failed both bounded optimizer attempts')
        prediction = fitted.get_forecast(steps=steps)
        point = np.asarray(prediction.predicted_mean, dtype=float)
        interval = np.asarray(prediction.conf_int(alpha=.2), dtype=float)
    if spec.transform == 'log':
        # Back-transformed central predictions minimize absolute-error loss on
        # the transformed model; no held-out bias correction is fitted.
        point, interval = np.expm1(point), np.expm1(interval)
    result = tuple(np.maximum(0, values * scale) for values in (point, interval[:, 0], interval[:, 1]))
    if not all(np.isfinite(values).all() and (values < 1e18).all() for values in result):
        raise ValueError('SARIMA produced an invalid monetary prediction')
    return result
