"""Historical labels use a threshold known before each target day's sales."""
import numpy as np
import pandas as pd


def classify(series,window,options):
    rows=[]
    for stamp,actual in series.items():
        if window is None:
            cutoff=options.fixed_cutoff;count=None;fallback=False
        else:
            prior=series.loc[(series.index>=stamp-pd.Timedelta(days=window))&(series.index<stamp)]
            values=prior[prior>0];count=len(values);fallback=count<options.min_positive_days
            if fallback:cutoff=options.fixed_cutoff
            else:
                median=float(values.median());mad=float((values-median).abs().median())
                cutoff=median+options.multiplier*1.4826*mad
        rows.append({'date':str(stamp.date()),'cutoff':float(cutoff),'positive_history_days':count,
            'fallback_used':fallback,'bulk':bool(actual>cutoff) if pd.notna(actual) else None})
    labels=pd.DataFrame(rows,index=series.index)
    regular=series.mask(labels['bulk'].eq(True))
    return labels,regular
