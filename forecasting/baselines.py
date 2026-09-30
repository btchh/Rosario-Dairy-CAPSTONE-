import math
import pandas as pd

NAMES=('recent_median_28_days','same_weekday_last_week')


def paths(regular,dates):
    if any(stamp<=regular.index[-1] for stamp in dates):raise ValueError('Baseline cannot use target observations')
    values=regular.loc[regular.index>=regular.index[-1]-pd.Timedelta(days=27)].dropna()
    median=float(values.median()) if len(values) else None
    result={NAMES[0]:[median]*len(dates),NAMES[1]:[]}
    for stamp in dates:
        lag=stamp-pd.Timedelta(days=7*math.ceil((stamp-regular.index[-1]).days/7))
        value=regular.get(lag)
        result[NAMES[1]].append(float(value) if value is not None and pd.notna(value) else None)
    return result
