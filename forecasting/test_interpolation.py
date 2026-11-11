from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase

from .methods import eligibility, forecast, interpolate_internal_gaps


class InternalInterpolationTests(SimpleTestCase):
    def test_interpolates_bracketed_gaps_linearly(self):
        self.assertEqual(interpolate_internal_gaps([4.0, None, 10.0]), [4.0, 7.0, 10.0])
        self.assertEqual(interpolate_internal_gaps([4.0, None, None, 10.0]),
                         [4.0, 6.0, 8.0, 10.0])

    def test_preserves_zeroes_and_does_not_extrapolate_edges(self):
        self.assertEqual(interpolate_internal_gaps([None, 4.0, None, 0.0, None, 10.0, None]),
                         [None, 4.0, 2.0, 0.0, 5.0, 10.0, None])

    def test_training_window_cannot_interpolate_using_a_later_period(self):
        history = [4.0, None, 10.0]
        self.assertEqual(interpolate_internal_gaps(history[:2]), [4.0, None])
        self.assertEqual(interpolate_internal_gaps(history), [4.0, 7.0, 10.0])

    def test_forecast_fits_interpolated_training_points(self):
        values = [8.0] * 30
        values[14], values[15], values[16] = 4.0, None, 10.0
        captured = {}

        class Fit:
            mle_retvals = {'converged': True}

            @staticmethod
            def forecast(horizon):
                return np.ones(horizon)

        class Model:
            def __init__(self, endog, **kwargs):
                captured['endog'] = endog.copy()

            @staticmethod
            def fit(**kwargs):
                return Fit()

        with patch('forecasting.methods.SARIMAX', Model):
            result = forecast(values, 'sarima', 2, 7)

        scale = max(float(np.mean([value for value in values if value is not None])), 1.0)
        self.assertAlmostEqual(captured['endog'][15], 7.0 / scale)
        self.assertEqual(result, [scale, scale])

    def test_interpolation_does_not_count_as_observed_seasonal_history(self):
        values = [4.0] + [None] * 28 + [10.0]
        reason = eligibility(values, 'sarima', 'daily')
        self.assertIn('24 observed training periods', reason)
