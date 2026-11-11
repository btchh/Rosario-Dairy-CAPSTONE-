"""Prepare scope-specific recorded data and saved configurable forecasts."""
from datetime import date, timedelta
import numpy as np
from django.utils import timezone
from inventory.models import Category, Product
from .hierarchy import advance, start, cutoff_for, covered_cutoff
from .hierarchy_source import load_source
from .methods import METHODS, SEASONS, PERIOD_DEFAULTS, HORIZONS, decomposition, forecast
from .demand_analysis import classify
from .evaluation import compare
from .models import MonthlyModelRun

VERSION='configurable-demand-sarima-v3'


def catalog():
    return {'engine_version':VERSION,'business_date':timezone.localdate().isoformat(),
        'methods':[method for method in METHODS.values()
                   if method['implementation_status']=='implemented'],
        'default_horizons':PERIOD_DEFAULTS,'horizons':HORIZONS,
        'categories':list(Category.objects.order_by('name','pk').values('id','name','is_active')),
        'products':[{**p,'variant':p['variant'] or '', 'unit':p['unit'].strip() or 'unit'} for p in Product.objects.order_by('name','pk').values(
            'id','name','variant','unit','category_id','is_active')],
        'subcategory_support':{'available':False,'reason':'The inventory model has no subcategory relationship.'},
        'approaches':['quantitative','qualitative'],
        'accuracy_target':{'metric':'wape_percent','below_percent':8,'scope':'monthly_regular_revenue',
            'forecast_distance':'rolling_one_month_ahead','evaluation':'latest saved chronological 12-month test',
            'demand_target':None},
        'limitations':['Recorded fulfilled purchases are not unconstrained demand or all stock movements.',
            'No reliable future promotion/price covariates or identified causal interventions are available.']}


def prepare(config,source=None):
    source=source or load_source()
    period=config['period']; metric='quantity' if config['metric']=='demand' else 'revenue'
    selected={pid:p for pid,p in source['products'].items() if
        (config['scope']=='overall' or config['scope']=='category' and p['category_id']==config['scope_id']
         or config['scope']=='item' and pid==config['scope_id'])
        and (metric=='revenue' or p['unit']==config['unit'])}
    name=next(iter(selected.values()))['name'] if config['scope']=='item' and selected else (
        Category.objects.filter(pk=config['scope_id']).values_list('name',flat=True).first() if config['scope']=='category' else 'Overall')
    relevant=[f for f in source['facts'] if f['product_id'] in selected
        and (config['segment']=='combined' or f['segment']==config['segment'])]
    contributing={f['product_id'] for f in relevant if f[metric] is not None}
    data={'source_revision':source['revision'],'name':name or 'Unavailable scope','config':config,
        'catalog_count':len(selected),'observed_item_count':len(contributing),
        'unobserved_item_count':len(selected)-len(contributing),'dates':[],'values':[],
        'source_latest_sale':source['latest_day'].isoformat() if source['latest_day'] else None,
        'scope_basis':'Recorded quantities in the selected native unit.' if metric=='quantity' else 'Attributable net invoice revenue, including the explicit unallocated bucket for overall revenue.',
        'training_window_policy':'Default uses the latest uninterrupted ledger-covered week/month run and ends at the last completed covered period; trailing unrecorded periods become projection lead time, while interior gaps remain unknown. Explicit training dates retain their requested calendar window.',
        'coverage_basis':'The aggregate contains historically observed items only. Unobserved catalog products are counted separately; no history is invented.'}
    if not relevant or source['latest_day'] is None: return data
    cutoff=cutoff_for(source['latest_day'],period)
    end=date.fromisoformat(config['training_end']) if config.get('training_end') else covered_cutoff(source,period)-timedelta(days=1)
    begin=date.fromisoformat(config['training_start']) if config.get('training_start') else min(f['day'] for f in relevant)
    begin=max(begin,min(f['day'] for f in relevant))
    if not config.get('training_start'):
        # Match the established hierarchy's coverage policy. Trim disjoint
        # leading ledger runs without removing interior missing days or SKUs.
        coverage_period='weekly' if period=='daily' else period
        boundaries={start(day,coverage_period) for day in source['coverage_days']}
        before=[boundary for boundary in boundaries if boundary<=start(min(end,cutoff-timedelta(days=1)),coverage_period)]
        if before:
            recent=max(before)
            while advance(recent,coverage_period,-1) in boundaries:
                recent=advance(recent,coverage_period,-1)
            begin=max(begin,recent)
    first=start(begin,period)
    if config.get('training_start') and first < date.fromisoformat(config['training_start']): first=advance(first,period)
    rows={}
    for fact in relevant:
        key=start(fact['day'],period)
        if key < first or key >= cutoff or advance(key,period)-timedelta(days=1)>end: continue
        row=rows.setdefault(key,{'sum':0.,'known':False,'unknown':False})
        if fact[metric] is None: row['unknown']=True
        else: row['sum']+=float(fact[metric]); row['known']=True
    covered={start(day,period) for day in source['coverage_days']}
    day=first
    while day < cutoff and advance(day,period)-timedelta(days=1)<=end:
        row=rows.get(day)
        value=(None if row and row['unknown'] else round(row['sum'],2) if row and row['known']
               else 0. if contributing and day in covered else None)
        data['dates'].append(day); data['values'].append(value)
        day=advance(day,period)
        if len(data['dates'])>5000: raise ValueError('Training window exceeds 5,000 calendar periods; choose a shorter window.')
    return data


def accuracy_target():
    from .learning import ENGINE_VERSION
    run=MonthlyModelRun.objects.filter(engine_version=ENGINE_VERSION).order_by('-cutoff','-pk').first()
    data=run.findings if run else {}
    score=data.get('selected_test') or {}
    return {'metric':'wape_percent','below_percent':8,'scope':'monthly_regular_revenue',
        'forecast_distance':'rolling_one_month_ahead','test_start':data.get('test_start'),
        'test_end':data.get('test_end'),'test_periods':score.get('periods'),
        'wape_percent':score.get('wape_percent'),'model_run_id':run.pk if run else None,
        'historical_target_met':score.get('wape_percent') is not None and score['wape_percent']<8,
        'production_approval':False,'demand_target':None,
        'basis':'Separate aggregate regular-revenue model: historical corrected-ledger test only; this does not certify item demand or current long-distance projections.'}


def build_analysis(config,source=None):
    source=source or load_source(); data=prepare(config,source)
    values=data.pop('values'); dates=data.pop('dates')
    period=config['period']; horizon=config['horizon']
    current=start(timezone.localdate(),period)
    origin=advance(dates[-1],period) if dates else current
    lead_gap=0
    day=origin
    while day<current:
        lead_gap+=1; day=advance(day,period)
        if lead_gap>5000: raise ValueError('Training cutoff is too far from the current business period.')
    comparison=compare(values,dates,period,horizon,mode=config['mode'],method=config.get('method'),parameters=config.get('parameters'),lead_gap=lead_gap)
    classification=classify(values,dates,period)
    classification['measure']=config['metric']
    targets=[]; day=origin
    while day<advance(max(origin,current),period,horizon):
        targets.append(day); day=advance(day,period)
        if len(targets)>5000: raise ValueError('Projection exceeds 5,000 periods after training; choose a more recent training window.')
    selected=next((r for r in comparison['methods'] if r['method']==comparison['selected_method']),None)
    predictions=None; failure=None; components=None
    if selected and comparison['status']=='available' and config['mode']!='inspect':
        try:
            predictions=forecast(values,selected['method'],len(targets),SEASONS[period],selected['parameters'])
        except (ValueError,ArithmeticError,np.linalg.LinAlgError) as error: failure=str(error)
    if len(values)>=2*SEASONS[period] and all(v is not None for v in values):
        try:
            fitted=decomposition(values,SEASONS[period])
            components=[{'date':d.isoformat(),'actual':v,**{k:fitted[k][i] for k in fitted}}
                for i,(d,v) in enumerate(zip(dates,values))][-240:]
        except (ValueError,ArithmeticError,np.linalg.LinAlgError): pass
    residuals=[r['actual']-r['predicted'] for r in selected.get('test_rows',[])] if selected else []
    bounds=np.quantile(residuals,[.1,.9]) if len(residuals)>=8 else None
    points=[]
    keep_from=advance(max(origin,current),period,-3)
    for index,day in enumerate(targets):
        if index>=horizon and day<keep_from: continue
        value=round(predictions[index],2) if predictions is not None else None
        valid_bounds=bounds is not None and lead_gap <= index < lead_gap+horizon and value is not None
        points.append({'date':day.isoformat(),'value':value,'steps_ahead':index+1,
            'lower':round(max(0.,predictions[index]+bounds[0]),2) if valid_bounds else None,
            'upper':round(max(0.,predictions[index]+bounds[1]),2) if valid_bounds else None})
    inactive=config['scope']=='item' and any(not p['is_active'] for pid,p in source['products'].items() if pid==config['scope_id'])
    # Inactive items remain visible for historical analysis, but receive no operational future estimates.
    if inactive:
        for point in points: point.update(value=None,lower=None,upper=None)
    status='inactive_item' if inactive else 'available' if predictions is not None else 'fit_failed' if failure else 'insufficient_history'
    reason='Inactive catalog item: historical analysis only.' if inactive else failure or comparison.get('reason')
    series={'status':status,'method':comparison['selected_method'],'known_periods':sum(v is not None for v in values),
        'nonzero_periods':sum(v is not None and v>0 for v in values),'training_start':dates[0].isoformat() if dates else None,
        'supported_series':int(status=='available'),'total_series':1,'reason':reason,
        'history':[{'date':d.isoformat(),'actual':v} for d,v in zip(dates[-240:],values[-240:])],
        'forecast':points,'test':selected.get('test_rows',[]) if selected else [],'metrics':selected.get('test') if selected else None,
        'classification':classification,'selection':comparison['protocol']['rationale'],
        'evaluation_basis':'gap_matched',
        'range_basis':'Empirical signed test-error 10th/90th quantiles from gap-matched targets; not calibrated confidence limits. Bounds are withheld outside the tested distance window.'}
    return {**data,'engine_version':VERSION,'period':period,'horizon':horizon,
        'trained_through':(origin-timedelta(days=1)).isoformat() if dates else None,
        'projection_start':max(origin,current).isoformat(),'projection_gap_periods':max(0,len(targets)-horizon),
        'series':series,'classification':classification,'comparison':comparison,'decomposition':components,
        'accuracy_target':accuracy_target(),
        'evaluation_limitation':f'Configured validation/test reproduce the {lead_gap}-period gap and score distances {lead_gap+1}–{lead_gap+horizon} after each training origin. No recent observations are supplied or inferred. Default hierarchy scores use their separate original test horizon.'}
