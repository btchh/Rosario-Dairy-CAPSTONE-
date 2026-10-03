"""Revenue errors and measured coverage; zero-actual periods remain in WAPE."""
import numpy as np


def score(rows,positive_only=False):
    valid=[r for r in rows if r.get('actual') is not None and r.get('predicted') is not None
        and (not positive_only or r['actual']>0)]
    if not valid:return {'rows':0,'positive_rows':0,'mape_percent':None,'wape_percent':None,'mae_pesos':None,'coverage_percent':None,'interval_rows':0}
    actual=np.array([r['actual'] for r in valid]);pred=np.array([r['predicted'] for r in valid]);gap=np.abs(actual-pred)
    positive=actual>0;intervals=[r for r in valid if r.get('lower') is not None and r.get('upper') is not None]
    return {'rows':len(valid),'positive_rows':int(positive.sum()),
        'mape_percent':round(float((gap[positive]/actual[positive]).mean()*100),4) if positive.any() else None,
        'wape_percent':round(float(gap.sum()/actual.sum()*100),4) if actual.sum() else None,
        'mae_pesos':round(float(gap.mean()),2),
        'coverage_percent':round(sum(r['lower']<=r['actual']<=r['upper'] for r in intervals)/len(intervals)*100,2) if intervals else None,
        'interval_rows':len(intervals)}
