"""Shared metrics and untouched, later-test comparisons."""
import math
import numpy as np
from .methods import METHODS, SEASONS, eligibility, forecast, normalize_parameters, ForecastConfigurationError


def metrics(rows):
    if not rows: return None
    actual = np.array([r['actual'] for r in rows],dtype=float)
    predicted = np.array([r['predicted'] for r in rows],dtype=float)
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all() or np.any(actual < 0):
        raise ValueError('Evaluation requires finite values and nonnegative actuals.')
    error = predicted-actual
    nonzero = actual != 0
    return {'periods':len(rows),'mae':round(float(np.abs(error).mean()),4),
        'mse':round(float((error**2).mean()),4),'rmse':round(float(np.sqrt((error**2).mean())),4),
        'bias':round(float(error.mean()),4),
        'wape_percent':round(float(100*np.abs(error).sum()/np.abs(actual).sum()),4) if np.abs(actual).sum() else None,
        'mape_percent':round(float(100*np.mean(np.abs(error[nonzero]/actual[nonzero]))),4) if nonzero.any() else None,
        'mape_observations':int(nonzero.sum()),'mape_excluded_zero_actuals':int((~nonzero).sum())}


def observations(dates, actual, predicted):
    return [{'date':day.isoformat(),'actual':float(a),'predicted':round(float(p),6)}
            for day,a,p in zip(dates,actual,predicted) if a is not None]


def compare(values, dates, period, horizon, *, mode='automatic', method=None, parameters=None, lead_gap=0):
    # Each candidate uses the identical original calendar and two disjoint,
    # full-horizon blocks. Eligibility and selection never inspect later test values.
    n=len(values); distance=horizon+lead_gap; validation_start=n-2*distance; test_start=n-distance
    training=values[:max(0,validation_start)]
    rows=[]
    for key,spec in METHODS.items():
        if spec['implementation_status'] != 'implemented':
            continue
        params=parameters if mode=='manual' and key==method else None
        reason=eligibility(training,key,period,params)
        if validation_start < 1: reason='Not enough history for separate full-horizon validation and test blocks.'
        rows.append({'method':key,'name':spec['name'],'eligible':reason is None,'reason':reason,
            'parameters':normalize_parameters(key,params) if spec['implementation_status']=='implemented' else {},
            'validation':None,'test':None,'status':'eligible' if reason is None else 'ineligible','selected':False})
    result={'status':'insufficient_history','selected_method':None,'methods':rows,
        'protocol':{'basis':'fixed_origin_multi_step','selection_metric':'mae','horizon':horizon,'lead_gap':lead_gap,'forecast_distance_start':lead_gap+1,'forecast_distance_end':distance,
            'training_start':dates[0].isoformat() if dates else None,
            'training_end':dates[validation_start-1].isoformat() if validation_start > 0 else None,
            'validation_start':dates[validation_start+lead_gap].isoformat() if validation_start >= 0 and validation_start+lead_gap < n else None,
            'validation_end':dates[test_start-1].isoformat() if test_start > 0 else None,
            'test_start':dates[test_start+lead_gap].isoformat() if test_start >= 0 and test_start+lead_gap < n else None,
            'test_end':dates[-1].isoformat() if dates else None,
            'rationale':('Administrator-selected eligible method; the disjoint validation/test results do not replace that choice.' if mode=='manual' else 'Earlier gap-matched full-horizon validation MAE selects the automatic model. The disjoint later test never selects or replaces it.')}}
    if mode=='inspect': result['status']='inspected'; return result
    if mode=='manual' and not next((r['eligible'] for r in rows if r['method']==method),False):
        raise ForecastConfigurationError(next((r['reason'] for r in rows if r['method']==method),'Select an eligible method.'))
    if validation_start < 1:
        result['reason']=f'{n} recorded-calendar periods cannot supply training plus two {distance}-period gap-matched validation/test blocks.'
        return result
    if any(sum(v is not None for v in block) < max(2,math.ceil(.8*horizon))
           for block in (values[validation_start+lead_gap:test_start], values[test_start+lead_gap:])):
        result['reason']='Validation and test each require at least 80% observed targets and two scored observations.'
        return result
    for row in rows:
        if not row['eligible']: continue
        try:
            predictions=forecast(training,row['method'],distance,SEASONS[period],row['parameters'])[lead_gap:]
            row['validation']=metrics(observations(dates[validation_start+lead_gap:test_start],values[validation_start+lead_gap:test_start],predictions))
            row['status']='validated'
        except (ValueError,ArithmeticError,np.linalg.LinAlgError) as error:
            row.update(status='fit_failed',reason=str(error))
    eligible=[r for r in rows if r['validation'] is not None]
    if not eligible:
        result['reason']='No method could be validated on the requested training window and forecast distances. Review method eligibility below.'
        return result
    selected=method if mode=='manual' else min(eligible,key=lambda r:(r['validation']['mae'],list(METHODS).index(r['method'])))['method']
    result['selected_method']=selected
    # Selection is frozen before any later-test model is fitted or scored.
    for row in eligible:
        row['selected']=row['method']==selected
        try:
            predictions=forecast(values[:test_start],row['method'],distance,SEASONS[period],row['parameters'])[lead_gap:]
            test_rows=observations(dates[test_start+lead_gap:],values[test_start+lead_gap:],predictions)
            row.update(test=metrics(test_rows),test_rows=test_rows,status='evaluated')
        except (ValueError,ArithmeticError,np.linalg.LinAlgError) as error:
            row.update(status='test_fit_failed',reason=str(error))
    result['status']='available' if next(r for r in rows if r['method']==selected)['test'] else 'test_fit_failed'
    return result
