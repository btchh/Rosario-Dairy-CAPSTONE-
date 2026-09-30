"""Chronological selection, fixed-origin calendar planning and honest diagnostics."""
import numpy as np
import pandas as pd
from dataclasses import replace
from .engine import normalize,walk,Predictor,CANDIDATES,SIMPLE_CANDIDATES,MIN_OBSERVED
from .cutoffs import classify
from .calendar import complete_periods,bulk_estimate,next_period
from .metrics import score,comparisons
from .baselines import paths,NAMES
from .contract import VERSION,LIMITATIONS,period_acceptance

VALIDATION_DAYS=60
TEST_DAYS=90
PERIOD_TESTS={'weekly':12,'monthly':6,'yearly':5}


def acceptance(metrics):
    return period_acceptance(metrics)


def split_origin(series,options=None):
    # Calendar boundaries are chosen without looking at target values or holes.
    from .calendar import bounds
    last=series.index[-1]
    starts=[last-pd.Timedelta(days=TEST_DAYS-1)]
    week_start,week_end=bounds(last,'weekly')
    if week_end>last:week_end=week_start-pd.Timedelta(days=1)
    starts.append(week_end-pd.Timedelta(days=7*PERIOD_TESTS['weekly']-1))
    month_start,month_end=bounds(last,'monthly')
    if month_end>last:month_start=bounds(month_start-pd.Timedelta(days=1),'monthly')[0]
    starts.append(month_start-pd.DateOffset(months=PERIOD_TESTS['monthly']-1))
    completed_year=last.year if last.month==12 and last.day==31 else last.year-1
    annual_start=pd.Timestamp(completed_year-PERIOD_TESTS['yearly']+1,1,1)
    if annual_start-pd.DateOffset(years=5)>=series.index[0]:starts.append(annual_start)
    origin=min(starts)
    if options and options.profile_trained_through:
        origin=max(origin,pd.Timestamp(options.profile_trained_through)+pd.Timedelta(days=1))
    return origin


def plan(point,lower,upper,bulk,days):
    if bulk['status']!='ready':return {'status':'insufficient_history','regular':None,'combined':None}
    weight=max(0.,1-bulk['expected_count']/days)
    regular={'conditional_revenue':round(float(point),2),'expected_non_bulk_day_fraction':weight,
        'point_kind':'monetary_mean',
        'predicted_revenue':round(float(point)*weight,2),
        'lower_bound':round(float(lower)*weight,2),'upper_bound':round(float(upper)*weight,2),
        'bound_kind':'sum_of_native_daily_80_percent_bounds_times_regular_day_fraction'}
    combined={'predicted_revenue':round(regular['predicted_revenue']+bulk['expected_revenue'],2),
        'lower_bound':round(regular['lower_bound']+bulk['min_revenue'],2),
        'upper_bound':round(regular['upper_bound']+bulk['max_revenue'],2),
        'range_kind':'planning_risk_range_not_calibrated_prediction_interval'}
    return {'status':'ready','regular':regular,'combined':combined}


def period_candidate(candidate,period,options):
    if period=='weekly' and options.weekly_regular_candidate:
        known={c.name:c for c in (*CANDIDATES,*SIMPLE_CANDIDATES)}
        if options.weekly_regular_candidate not in known:raise ValueError('Unknown weekly SARIMA candidate')
        return replace(known[options.weekly_regular_candidate],training_days=options.weekly_regular_training_days,point_kind='mean')
    return candidate


def predict_period(series,labels,regular,start,end,candidate,options):
    history=series.loc[series.index<start];training=regular.loc[regular.index<start]
    days=(end-start).days+1
    period='yearly' if days>=365 and start.month==1 and start.day==1 else 'weekly' if days==7 and start.weekday()==0 else 'monthly'
    candidate=period_candidate(candidate,period,options)
    base={'date':str(start.date()),'end_date':str(end.date()),'trained_through':str(history.index[-1].date()) if len(history) else None,
        'method':candidate.name,'days':days}
    base['model']={'training_days':candidate.training_days,'point_kind':'monetary_mean'}
    bulk=bulk_estimate(history,labels,period,start,options)
    base['bulk']=bulk
    if bulk['status']!='ready' or training.notna().sum()<MIN_OBSERVED:return {**base,'status':'insufficient_history','regular':None,'combined':None,'baselines':{}}
    try:
        model=Predictor(training,candidate)
        horizon=(end-training.index[-1]).days;skip=(start-training.index[-1]).days-1
        # Period planning adds monetary expectations, rather than transformed medians.
        point,lower,upper=model.path(horizon,point_kind='mean')
        raw=float(point[skip:].sum())
        parts=plan(raw,float(lower[skip:].sum()),float(upper[skip:].sum()),bulk,days)
        baseline_paths=paths(training,pd.date_range(start,end));baselines={}
        for name,values in baseline_paths.items():
            baselines[name]=plan(sum(values),0,0,bulk,days)['combined']['predicted_revenue'] if all(v is not None for v in values) and bulk['status']=='ready' else None
        baselines['zero_bulk']=round(raw,2)
        return {**base,**parts,'baselines':baselines}
    except (ValueError,np.linalg.LinAlgError) as exc:
        return {**base,'status':'model_unavailable','regular':None,'combined':None,'baselines':{},'reason':str(exc)}


def period_scores(rows):
    combined=[];regular=[];bulk=[]
    for row in rows:
        part=row.get('combined');normal=row.get('regular');risk=row['bulk']
        combined.append({'actual':row['actual'],'predicted':part['predicted_revenue'] if part else None,
            'lower':part['lower_bound'] if part else None,'upper':part['upper_bound'] if part else None,
            'baselines':row.get('baselines',{})})
        regular.append({'actual':row['actual_regular_revenue'],'predicted':normal['predicted_revenue'] if normal else None,
            'lower':normal['lower_bound'] if normal else None,'upper':normal['upper_bound'] if normal else None,
            'baselines':{name:(value-risk['expected_revenue'] if value is not None and risk['expected_revenue'] is not None else None)
                for name,value in row.get('baselines',{}).items() if name in NAMES}})
        bulk.append({'actual':row['actual_bulk_revenue'],'predicted':risk['expected_revenue'],
            'lower':risk['min_revenue'],'upper':risk['max_revenue'],'baselines':{'zero_bulk':0.}})
    bulk_score=score(bulk)
    available=[r for r in rows if r['bulk']['status']=='ready']
    bulk_score['count_coverage_percent']=round(100*sum(r['bulk']['min_count']<=r['actual_bulk_count']<=r['bulk']['max_count'] for r in available)/len(available),2) if available else None
    return {'combined':score(combined),'regular':score(regular,positive_only=True),'bulk':bulk_score,
        'combined_baselines':comparisons(combined,(*NAMES,'zero_bulk'),period=True),
        'regular_baselines':comparisons(regular,NAMES,period=True,positive_only=True),
        'bulk_baselines':comparisons(bulk,('zero_bulk',),period=True),
        'status':'too_few_to_conclude' if len([r for r in combined if r['predicted'] is not None])<5 else 'evaluated'}


def evaluate_setup(series,window,options,origin,progress=None,candidates=CANDIDATES):
    labels,regular=classify(series,window,options)
    available=(*candidates,*SIMPLE_CANDIDATES) if candidates is CANDIDATES else candidates
    if options.regular_candidate_names:
        selected_names=set(options.regular_candidate_names)
        if selected_names-set(c.name for c in available):raise ValueError('Unknown configured SARIMA candidate')
        available=tuple(c for c in available if c.name in selected_names)
    else:available=candidates
    candidates=tuple(replace(c,training_days=options.regular_training_days,point_kind=options.regular_point_kind) for c in available)
    name='fixed_100k' if window is None else f'dynamic_{window}'
    base={'setup':name,'window':window,'cutoff_multiplier':options.multiplier,
        'fallback_days':int(labels['fallback_used'].sum()),'labels':labels.to_dict('records'),
        'status':'insufficient_history','regular_metrics':score([]),'daily_rows':[],
        'validation_rows':[],'validation_metrics':None,'method':None,'periods':{},'next_day':None,
        'warnings':[LIMITATIONS]}
    base['regular_model']={'training_days':options.regular_training_days,'daily_point_kind':options.regular_point_kind,
        'planning_point_kind':'monetary_mean','candidate_names':list(options.regular_candidate_names)}
    if regular.loc[regular.index<origin].notna().sum()<MIN_OBSERVED:return base
    validation_start=origin-pd.Timedelta(days=VALIDATION_DAYS)
    train=regular.loc[regular.index<validation_start]
    validation=regular.loc[(regular.index>=validation_start)&(regular.index<origin)]
    if train.notna().sum()<60 or len(validation)!=VALIDATION_DAYS:return base
    ranked=[]
    for candidate in candidates:
        if progress:progress(f'{name}: validating {candidate.name}')
        try:
            rows,_=walk(train,validation,candidate);metrics=score(rows,positive_only=True)
            if metrics['positive_rows']>=30:ranked.append((metrics['mape_percent'],candidate,metrics,rows))
        except (ValueError,np.linalg.LinAlgError) as exc:
            if progress:progress(f'{name}: unavailable {candidate.name}: {exc}')
    chosen=None
    # This fit viability check also ends before every test target.
    for _,candidate,validation_metrics,validation_rows in sorted(ranked,key=lambda r:(r[0],r[1].name)):
        try:Predictor(regular.loc[regular.index<origin],candidate)
        except (ValueError,np.linalg.LinAlgError):continue
        chosen=candidate;break
    if chosen is None:return {**base,'status':'model_unavailable'}
    test_start=series.index[-1]-pd.Timedelta(days=TEST_DAYS-1)
    test=regular.loc[regular.index>=test_start]
    if progress:progress(f'{name}: testing daily {chosen.name}')
    daily_rows,state=walk(regular.loc[regular.index<test_start],test,chosen)
    for row in daily_rows:
        stamp=pd.Timestamp(row['date']);original=series.loc[stamp];label=labels.loc[stamp]
        row.update(original_actual=float(original) if pd.notna(original) else None,
            bulk=bool(label['bulk']) if label['bulk'] is not None else None,cutoff=float(label['cutoff']),fallback_used=bool(label['fallback_used']))
        row['baselines']={key:value[0] for key,value in paths(regular.loc[regular.index<stamp],[stamp]).items()}
    metrics=score(daily_rows,positive_only=True)
    result={**base,'status':'evaluated','method':chosen.name,'validation_metrics':validation_metrics,
        'validation_rows':validation_rows,'validation_start':str(validation_start.date()),
        'validation_end':str((origin-pd.Timedelta(days=1)).date()),
        'candidates':[{'method':c.name,'validation_metrics':m} for _,c,m,_ in ranked],
        'daily_rows':daily_rows,'regular_metrics':metrics,'refit_failures':state.refit_failures,
        'regular_baselines':comparisons(daily_rows,NAMES,positive_only=True),
        'zero_regular_days':sum(r['actual']==0 for r in daily_rows),
        'excluded_bulk_test_days':sum(r['bulk'] is True for r in daily_rows)}
    try:
        final=Predictor(regular,chosen);point,lower,upper=final.path(1)
        result['next_day']={'date':str((series.index[-1]+pd.Timedelta(days=1)).date()),
            'point_kind':chosen.point_kind,
            'trained_through':str(series.index[-1].date()),'predicted_revenue':round(float(point[0]),2),
            'lower_bound':round(float(lower[0]),2),'upper_bound':round(float(upper[0]),2),'interval_nominal_percent':80}
    except (ValueError,np.linalg.LinAlgError):result['warnings'].append('Current regular SARIMA fit is unavailable.')
    # Whole-period validation paths are fixed at the period's start, not summed
    # one-step forecasts that have already observed days within that period.
    prior_series=series.loc[series.index<origin];prior_labels=labels.loc[labels.index<origin]
    prior_regular=regular.loc[regular.index<origin]
    validation_periods={}
    for period in PERIOD_TESTS:
        rows=[]
        targets=[r for r in complete_periods(prior_series,prior_labels,period) if r['start']>=validation_start]
        for actual in targets:
            row=predict_period(prior_series,prior_labels,prior_regular,actual['start'],actual['end'],chosen,options)
            row.update(actual=actual['actual'],actual_bulk_revenue=actual['bulk_revenue'],actual_bulk_count=actual['bulk_count'],actual_regular_revenue=actual['regular_revenue'])
            rows.append(row)
        validation_periods[period]={'rows':rows,'metrics':period_scores(rows)}
    result['period_validation']=validation_periods
    for period,count in PERIOD_TESTS.items():
        actuals=[r for r in complete_periods(series,labels,period) if r['start']>=origin]
        rows=[]
        for actual in actuals[-count:]:
            if progress:progress(f'{name}: {period} before {actual["start"].date()}')
            row=predict_period(series,labels,regular,actual['start'],actual['end'],chosen,options)
            row.update(actual=actual['actual'],actual_bulk_revenue=actual['bulk_revenue'],
                actual_bulk_count=actual['bulk_count'],actual_regular_revenue=actual['regular_revenue'])
            combined=row.get('combined')
            row['bulk_absolute_error_pesos']=round(abs(actual['bulk_revenue']-row['bulk']['expected_revenue']),2) if row['bulk']['expected_revenue'] is not None else None
            rows.append(row)
        start,end=next_period(series.index[-1],period)
        next_point=predict_period(series,labels,regular,start,end,chosen,options)
        period_metrics=period_scores(rows)
        result['periods'][period]={'rows':rows,'metrics':period_metrics,'status':acceptance(period_metrics['combined']),'next_period':next_point,
            'required_test_periods':count,'observed_test_periods':len(rows)}
    return result


def evaluate(series,options,progress=None,candidates=CANDIDATES):
    series=normalize(series)
    if len(series)<240 or series.notna().sum()<MIN_OBSERVED:raise ValueError('Insufficient history for validation and held-out testing')
    origin=split_origin(series,options)
    setups={}
    for window in (None,*options.windows):
        value=evaluate_setup(series,window,options,origin,progress,candidates)
        setups[value['setup']]=value
    dynamic=[s for s in setups.values() if s['window'] is not None and 'period_validation' in s]
    selection=[];selected=None
    if dynamic:
        by_setup={s['setup']:{r['date']:{'actual':r['actual'],'predicted':r['combined']['predicted_revenue']} for r in s['period_validation']['weekly']['rows'] if r.get('combined')} for s in dynamic}
        shared=set.intersection(*(set(rows) for rows in by_setup.values()))
        if len(shared)>=5:
            for setup in dynamic:
                metrics=score([by_setup[setup['setup']][d] for d in sorted(shared)])
                selection.append({'setup':setup['setup'],'window':setup['window'],'shared_rows':len(shared),'validation_wape_percent':metrics['wape_percent']})
            selected=min(selection,key=lambda s:(s['validation_wape_percent'],s['window']))['setup']
    return {'version':VERSION,'configuration':options.values(),'selected_setup':selected,
        'selection_rule':'Lowest combined weekly validation WAPE on shared complete periods; fixed cutoff is comparison only.',
        'selection_status':'selected' if selected else 'insufficient_validation',
        'selection':selection,'selection_trained_through':str((origin-pd.Timedelta(days=1)).date()),
        'data_start':str(series.index[0].date()),'data_end':str(series.index[-1].date()),
        'original_observed_days':int(series.notna().sum()),'missing_days':int(series.isna().sum()),
        'setups':setups,'limitations':LIMITATIONS}
