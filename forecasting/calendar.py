from decimal import Decimal
import math
import pandas as pd

def money_total(values):
    return float(sum((Decimal(str(value)) for value in values),Decimal(0)).quantize(Decimal("0.01")))

def money_mean(values):
    return float((sum((Decimal(str(value)) for value in values),Decimal(0))/len(values)).quantize(Decimal("0.01")))

def money_weighted_mean(values,weights):
    weights=[Decimal(str(w)) for w in weights]
    return float((sum((Decimal(str(v))*w for v,w in zip(values,weights)),Decimal(0))/sum(weights)).quantize(Decimal('0.01')))


def bounds(stamp,period):
    stamp=pd.Timestamp(stamp).normalize()
    if period=='weekly':
        start=stamp-pd.Timedelta(days=stamp.weekday());return start,start+pd.Timedelta(days=6)
    if period=='yearly':return pd.Timestamp(stamp.year,1,1),pd.Timestamp(stamp.year,12,31)
    if period!='monthly':raise ValueError('Expected weekly, monthly or yearly period')
    value=stamp.to_period('M');return value.start_time.normalize(),value.end_time.normalize()


def complete_periods(series,labels,period):
    if series.empty:return []
    start,_=bounds(series.index[0],period);rows=[]
    while start<=series.index[-1]:
        _,end=bounds(start,period);dates=pd.date_range(start,end)
        values=series.reindex(dates)
        if values.notna().all():
            flags=labels.reindex(dates)['bulk'].astype(bool)
            rows.append({'start':start,'end':end,'actual':money_total(values),
                'bulk_revenue':money_total(values[flags]),'bulk_count':int(flags.sum()),
                'regular_revenue':money_total(values[~flags])})
        start=end+pd.Timedelta(days=1)
    return rows


def next_period(last,period):
    start,end=bounds(last+pd.Timedelta(days=1),period)
    if start<=last:start=end+pd.Timedelta(days=1)
    _,end=bounds(start,period)
    return start,end


def bulk_estimate(history,labels,period,start,options):
    start=pd.Timestamp(start)
    # Slice both inputs before the origin, even if the caller passed full data.
    history=history.loc[history.index<start];labels=labels.loc[labels.index<start]
    past=[row for row in complete_periods(history,labels,period) if row['end']<start]
    lookback=getattr(options,f'{period}_lookback')
    half_life=options.weekly_bulk_half_life if period=='weekly' else None
    past=past[-lookback:]
    base={'status':'insufficient_history','kind':'historical_bulk_risk_range_no_event_dates',
        'lookback_periods':lookback,'required_periods':options.min_bulk_periods,'observed_periods':len(past),
        'weighting':'recency_weighted_mean' if half_life is not None else 'arithmetic_mean',
        'half_life_periods':half_life,
        'trained_through':str(history.index[-1].date()) if len(history) else None,
        'expected_count':None,'min_count':None,'max_count':None,
        'expected_revenue':None,'min_revenue':None,'max_revenue':None,
        'past_periods':[{'date':str(row['start'].date()),'end_date':str(row['end'].date()),
            'bulk_count':row['bulk_count'],'bulk_revenue':row['bulk_revenue']} for row in past]}
    if len(past)<options.min_bulk_periods:return base
    counts=[row['bulk_count'] for row in past];amounts=[row['bulk_revenue'] for row in past]
    weights=[math.exp2(-(len(past)-1-i)/half_life) for i in range(len(past))] if half_life is not None else None
    count=sum(c*w for c,w in zip(counts,weights))/sum(weights) if weights else sum(counts)/len(counts)
    return {**base,'status':'ready','expected_count':count,
        'min_count':min(counts),'max_count':max(counts),
        'expected_revenue':money_weighted_mean(amounts,weights) if weights else money_mean(amounts),
        'min_revenue':min(amounts),'max_revenue':max(amounts)}
