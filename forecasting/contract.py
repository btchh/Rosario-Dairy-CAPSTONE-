"""Versioned forecasting contract and publication threshold."""
from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
import json
import math

VERSION = 'daily-sarima-v1'
TARGET_PERCENT = 30.0
LIMITATIONS = (
    '2025 has already been inspected during development; these are retrospective backtests, '
    'not independent evidence of future accuracy. Candidate selection uses only periods ending before 2025. '
    'Only isolated 2022 tickets exist, so model training starts with the complete 2023 workbooks. '
    'Days without a recorded ticket count as zero recorded revenue within those workbooks. '
    'Milk-feeding labels identify recorded customers, not future delivery commitments. '
    'Yearly error is based on one test year and is insufficient for a 30% acceptance claim. '
    'Daily SARIMA uses seven-day calendar seasonality and sums daily monetary expectations. '
    'Reporting ranges use pre-2025 period validation errors; their future coverage is not guaranteed. '
    'Fixed-origin annual paths have no validated interval. '
    'Unscheduled milk-feeding contracts cannot be inferred reliably from ticket history alone.'
)


def period_acceptance(metrics):
    if metrics.get('failed_rows', 0):
        return 'model_unavailable'
    error = metrics.get('wape_percent')
    if metrics.get('rows', 0) < 5 or error is None or not math.isfinite(error):
        return 'insufficient_evaluation'
    return 'accepted' if error <= TARGET_PERCENT else 'rejected'


@dataclass(frozen=True)
class Options:
    model: str = 'sarima'
    training_start: str = '2023-01-01'
    validation_start: str = '2024-07-01'
    test_start: str = '2025-01-01'
    test_end: str = '2025-12-31'
    weekly_validation_stride: int = 2

    def __post_init__(self):
        start, validation, test, end = (date.fromisoformat(value) for value in (
            self.training_start, self.validation_start, self.test_start, self.test_end))
        if not start < validation < test <= end:
            raise ValueError('Expected ordered training, validation, and test dates')
        if self.weekly_validation_stride < 1:
            raise ValueError('Weekly validation stride must be positive')

    def values(self):
        return asdict(self)

    def signature(self):
        return sha256(json.dumps({'version':VERSION, **self.values()}, sort_keys=True).encode()).hexdigest()


def options(scope='real'):
    if scope != 'real':
        raise ValueError('Only real sales are supported')
    return Options()
