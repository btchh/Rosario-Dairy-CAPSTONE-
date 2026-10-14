"""Reproducible scenarios from historical month/weekday-aligned revenue blocks.

These scenarios are experiments, never actuals for measuring forecast acceptance.
"""
from calendar import isleap
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import numpy as np
import pandas as pd

METHOD = 'seasonal-week-bootstrap-v1'
YEARS = (2023, 2024, 2025)
SEGMENTS = ('feeding', 'other_sales')


def generate(components, seed=2026):
    """Sample whole historical weeks jointly across segments, then scale trend.

    Calendar month and weekday alignment retains broad seasonality. Joint blocks
    retain observed within-week bursts, zeros, and cross-segment relationships.
    Annual normalization separates donor-year level from seasonal shape. Growth
    is the median observed annual change, capped at +/-25% for extrapolation.
    """
    frames = {}
    for year in YEARS:
        index = pd.date_range(f'{year}-01-01', f'{year}-12-31')
        frame = pd.DataFrame(index=index)
        for segment in SEGMENTS:
            values = components[segment]
            frame[segment] = [float(values.get(stamp.date(), 0)) for stamp in index]
        if not np.isfinite(frame.to_numpy()).all() or (frame.to_numpy() < 0).any():
            raise ValueError('Historical revenue must be finite and nonnegative')
        frames[year] = frame

    means = {year: float(frame.sum(axis=1).mean()) for year, frame in frames.items()}
    if any(value <= 0 for value in means.values()):
        raise ValueError('Each source year must contain positive revenue')
    changes = [means[year] / means[year - 1] - 1 for year in YEARS[1:]]
    growth = float(np.clip(np.median(changes), -0.25, 0.25))
    target_mean = means[2025] * (1 + growth)
    rng = np.random.default_rng(seed)
    rows, donors = [], []
    start, end = date(2026, 1, 1), date(2026, 12, 31)
    while start <= end:
        length = min(7 - start.weekday(), (end - start).days + 1)
        year = int(rng.choice(YEARS, p=[0.2, 0.3, 0.5]))
        frame = frames[year]
        candidates = [stamp for stamp in frame.index
                      if stamp.month == start.month and stamp.weekday() == start.weekday()
                      and stamp + pd.Timedelta(days=length - 1) <= frame.index[-1]]
        donor = candidates[int(rng.integers(len(candidates)))]
        scale = target_mean / means[year]
        donors.append({'target_start': str(start), 'source_start': str(donor.date()),
                       'days': length, 'scale': scale})
        for offset in range(length):
            values = frame.loc[donor + pd.Timedelta(days=offset)]
            row = {'date': start + timedelta(days=offset)}
            for segment in SEGMENTS:
                row[segment + '_revenue'] = Decimal(str(float(values[segment]) * scale)).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP)
            rows.append(row)
        start += timedelta(days=length)

    assert len(rows) == 365 + isleap(2026)
    metadata = {
        'data_kind': 'synthetic', 'source_start': '2023-01-01', 'source_end': '2025-12-31',
        'source_annual_daily_means': means, 'annual_changes': changes,
        'applied_growth': growth, 'growth_cap': 0.25,
        'donor_year_weights': [0.2, 0.3, 0.5], 'donor_blocks': donors,
        'limitations': 'Scenario only; not observed sales or independent WAPE evidence. '
                       'No-ticket days within imported workbook coverage are treated as zero. '
                       'Month/weekday alignment does not preserve movable holidays or future contracts. '
                       'The sampled annual total is not forced to match the expected annual level.',
    }
    return rows, metadata
