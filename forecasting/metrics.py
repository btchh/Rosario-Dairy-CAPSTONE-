"""Matched-row diagnostics; extrema ranges have measured, not nominal, coverage."""
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


def comparisons(rows,baseline_names,period=False,positive_only=False):
    result={}
    for name in baseline_names:
        targets=[r for r in rows if r.get('actual') is not None and (not positive_only or r['actual']>0)]
        shared=[r for r in targets if r.get('predicted') is not None and r.get('baselines',{}).get(name) is not None]
        own=score(shared,positive_only);baseline=score([{**r,'predicted':r['baselines'][name],
            'lower':0. if name=='zero_bulk' else None,'upper':0. if name=='zero_bulk' else None} for r in shared],positive_only)
        status='too_few_to_conclude' if period and len(shared)<5 else 'unavailable' if not shared else 'compared'
        result[name]={'shared_rows':len(shared),'unavailable_rows':len(targets)-len(shared),
            'status':status,'model':own,'baseline':baseline,
            'beats_baseline_mape':own['mape_percent']<baseline['mape_percent'] if own['mape_percent'] is not None and baseline['mape_percent'] is not None else None,
            'beats_baseline_wape':own['wape_percent']<baseline['wape_percent'] if own['wape_percent'] is not None and baseline['wape_percent'] is not None else None}
    return result
