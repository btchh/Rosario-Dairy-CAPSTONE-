"""Configuration is included in snapshot identity; it cannot silently reuse fits."""
from dataclasses import dataclass,asdict
from hashlib import sha256
import json,math
from django.conf import settings

VERSION='period-hybrid-v3'
TARGET_PERCENT=30.0
LIMITATIONS=('Dynamic bulk classification is a statistical rule, not verified bulk orders. '
    'History is limited and this is a retrospective backtest on previously inspected data. '
    'Bulk dates cannot be predicted. Historical extrema and summed daily SARIMA bounds '
    'form a planning risk range, not a calibrated combined prediction interval.')

def period_acceptance(metrics):
    error=metrics.get('wape_percent')
    if metrics.get('rows',0)<5 or error is None or not math.isfinite(error):return 'insufficient_evaluation'
    return 'accepted' if error<=TARGET_PERCENT else 'rejected'

@dataclass(frozen=True)
class Options:
    windows: tuple=(60,90)
    multiplier: float=3.0
    min_positive_days: int=30
    fixed_cutoff: float=100_000.0
    weekly_lookback: int=12
    monthly_lookback: int=6
    yearly_lookback: int=5
    min_bulk_periods: int=5
    weekly_bulk_half_life: float | None=None
    weekly_regular_candidate: str | None=None
    weekly_regular_training_days: int | None=None
    profile_trained_through: str | None=None
    regular_training_days: int | None=None
    regular_point_kind: str='central'
    regular_candidate_names: tuple=()

    def __post_init__(self):
        if not self.windows or len(set(self.windows))!=len(self.windows) or any(not isinstance(n,int) or n<1 for n in self.windows):
            raise ValueError('Cutoff windows must be distinct positive calendar-day counts')
        if not math.isfinite(self.multiplier) or self.multiplier<0:raise ValueError('Invalid MAD multiplier')
        if not math.isfinite(self.fixed_cutoff) or self.fixed_cutoff<=0:raise ValueError('Invalid fallback cutoff')
        if self.min_positive_days<1 or self.min_bulk_periods<1:raise ValueError('Minimum sample counts must be positive')
        if min(self.weekly_lookback,self.monthly_lookback,self.yearly_lookback)<self.min_bulk_periods:
            raise ValueError('Bulk lookbacks must cover the minimum number of periods')
        if self.weekly_bulk_half_life is not None and (not math.isfinite(self.weekly_bulk_half_life) or self.weekly_bulk_half_life<=0):
            raise ValueError('Bulk recency half-life must be positive and finite')
        if self.weekly_regular_candidate is not None and (not isinstance(self.weekly_regular_candidate,str) or not self.weekly_regular_candidate):
            raise ValueError('Invalid weekly SARIMA candidate')
        if self.profile_trained_through is not None:
            from datetime import date
            date.fromisoformat(self.profile_trained_through)
        if self.regular_training_days is not None and (not isinstance(self.regular_training_days,int) or self.regular_training_days<30):
            raise ValueError('Regular training window must cover at least 30 calendar days')
        if self.weekly_regular_training_days is not None and (not isinstance(self.weekly_regular_training_days,int) or self.weekly_regular_training_days<30):
            raise ValueError('Weekly regular training window must cover at least 30 days')
        if self.regular_point_kind not in ('central','mean'):raise ValueError('Invalid regular point kind')
        if any(not isinstance(name,str) or not name for name in self.regular_candidate_names):raise ValueError('Invalid candidate names')

    def values(self):return asdict(self)
    def signature(self):return sha256(json.dumps(self.values(),sort_keys=True).encode()).hexdigest()


def options(scope='real'):
    if scope!='real':raise ValueError('Only real sales are supported')
    values=dict(getattr(settings,'FORECAST_OPTIONS',{}))
    values.update(getattr(settings,'FORECAST_SCOPE_OPTIONS',{}).get(scope,{}))
    if 'windows' in values:values['windows']=tuple(values['windows'])
    if 'regular_candidate_names' in values:values['regular_candidate_names']=tuple(values['regular_candidate_names'])
    return Options(**values)
