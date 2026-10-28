"""Describe observed regular-sales patterns before choosing a replacement model."""
from collections import defaultdict
from decimal import Decimal
from statistics import mean, median, pstdev

from .benchmarks import evaluate
from .data import SOURCE_POLICY

ANALYZER_VERSION = 'regular-sales-patterns-v4'
TARGET_WAPE_PERCENT = 15.0


def money(value):
    return format(Decimal(value).quantize(Decimal('0.01')), '.2f')


def correlation(values, lag):
    if len(values) < lag + 3:return None
    x, y = values[:-lag], values[lag:]
    mx, my = mean(x), mean(y)
    denominator = (sum((v-mx)**2 for v in x)*sum((v-my)**2 for v in y))**.5
    return round(sum((a-mx)*(b-my) for a,b in zip(x,y))/denominator,4) if denominator else None


def investigate(snapshot):
    monthly = {}
    customers = defaultdict(list)
    weekdays = {day: [] for day in range(7)}
    month_days = {name: [] for name in ('1–7','8–14','15–21','22–28','29–31')}
    regular = [sale for sale in snapshot.sales if sale.segment == 'regular']
    for sale in snapshot.sales:
        month = sale.day.strftime('%Y-%m')
        row = monthly.setdefault(month, {'month':month,'regular_revenue':Decimal('0'),
                    'feeding_revenue':Decimal('0'),'unclassified_revenue':Decimal('0'),
                    'regular_invoices':0,'feeding_invoices':0,'unclassified_invoices':0,'days':set()})
        row[sale.segment+'_revenue'] += sale.revenue
        row[sale.segment+'_invoices'] += 1
        row['days'].add(sale.day)
        if sale.segment == 'regular':
            customers[sale.customer_key].append(sale)
            weekdays[sale.day.weekday()].append(sale)
            bucket = min((sale.day.day-1)//7,4)
            month_days[list(month_days)[bucket]].append(sale)
    monthly_rows=[]
    for key,row in sorted(monthly.items()):
        monthly_rows.append({**{k:v for k,v in row.items() if k!='days'},
                             'recorded_days':len(row['days'])})
        for field in ('regular_revenue','feeding_revenue','unclassified_revenue'):
            monthly_rows[-1][field]=money(row[field])
    through=max((s.day for s in snapshot.sales),default=None)
    total=sum((sale.revenue for sale in regular),Decimal('0'))
    profile=[]
    for key,rows in customers.items():
        dates=sorted(set(sale.day for sale in rows))
        gaps=[(b-a).days for a,b in zip(dates,dates[1:])]
        revenue=sum((sale.revenue for sale in rows),Decimal('0'))
        last=dates[-1]
        days_since_last=(through-last).days
        typical=median(gaps) if gaps else None
        profile.append({
            'customer_key':key,'customer_name':rows[-1].customer_name,
            'invoice_count':len(rows),'purchase_days':len(dates),
            'active_months':len(set(sale.day.strftime('%Y-%m') for sale in rows)),
            'first_purchase':dates[0].isoformat(),'last_purchase':last.isoformat(),
            'days_since_last_purchase':days_since_last,
            'activity_reference_date':through.isoformat(),
            'revenue':money(revenue),'revenue_share_percent':round(float(revenue/total*100),4) if total else None,
            'median_invoice_value':round(median(float(sale.revenue) for sale in rows),2),
            'median_gap_days':typical,
            'gap_cv':round(pstdev(gaps)/mean(gaps),4) if len(gaps)>=3 and mean(gaps)>0 else None,
            'possible_historical_lapse':days_since_last>max(30,3*typical) if typical is not None else None,
        })
    profile.sort(key=lambda row:(-Decimal(row['revenue']),row['customer_key']))
    def calendar(rows,key):
        return {key:'', 'invoice_count':len(rows),'revenue':money(sum((s.revenue for s in rows),Decimal('0'))),
                'purchase_days':len(set(s.day for s in rows))}
    weekday_rows=[]
    for day,rows in weekdays.items():
        entry=calendar(rows,'weekday');entry['weekday']=('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')[day]
        weekday_rows.append(entry)
    day_rows=[]
    for name,rows in month_days.items():
        entry=calendar(rows,'days_of_month');entry['days_of_month']=name;day_rows.append(entry)
    # Ledger totals remain useful even when a segment has no invoices. Such
    # totals are not evidence that its unobserved revenue was a true zero.
    # Use the same completed, observed regular months for every monthly analysis.
    cutoff_month = snapshot.as_of.strftime('%Y-%m')
    completed_months = [row for row in monthly_rows if row['month'] < cutoff_month]
    analysis_months = [row for row in completed_months if row['regular_invoices'] > 0]
    # Correlations use only the latest uninterrupted run of observed months.
    contiguous=[]
    for row in analysis_months:
        y,m=map(int,row['month'].split('-'));ordinal=y*12+m
        if contiguous and ordinal!=contiguous[-1][0]+1:contiguous=[]
        contiguous.append((ordinal,float(row['regular_revenue'])))
    values=[value for _,value in contiguous]
    prior_year={row['month']:row for row in analysis_months}
    changes=[]
    for row in analysis_months:
        year,month=map(int,row['month'].split('-'))
        previous=prior_year.get(f'{year-1:04}-{month:02}')
        if previous and Decimal(previous['regular_revenue'])>0:
            current,old=Decimal(row['regular_revenue']),Decimal(previous['regular_revenue'])
            changes.append({'month':row['month'],'previous_year_revenue':money(old),
                            'regular_revenue':money(current),'change_percent':round(float((current/old-1)*100),4)})
    benchmarks=evaluate(analysis_months)
    return {
        'analyzer_version':ANALYZER_VERSION,'source_revision':snapshot.revision,
        'as_of':snapshot.as_of.isoformat(),'observed_through':through.isoformat() if through else None,
        'stage':'pattern_investigation','eligible_for_publication':False,
        'target':{'segment':'regular','measure':'monthly_revenue_wape','below_percent':TARGET_WAPE_PERCENT},
        'source_policy':SOURCE_POLICY,'coverage_verified':False,
        'data_quality':{'included_invoices':len(snapshot.sales),'regular_invoices':len(regular),
                        'feeding_invoices':sum(s.segment=='feeding' for s in snapshot.sales),
                        'unclassified_invoices':sum(s.segment=='unclassified' for s in snapshot.sales),
                        'unclassified_revenue':money(sum((s.revenue for s in snapshot.sales if s.segment=='unclassified'),Decimal('0'))),
                        'negative_invoice_count':sum(s.revenue<0 for s in snapshot.sales),
                        'unlinked_regular_invoices':sum(not s.customer_key.startswith('customer:') for s in regular),
                        'excluded_generated_invoices':snapshot.excluded_demo_count,
                        'excluded_non_dairy_invoices':snapshot.excluded_non_dairy_count,
                        'days_since_latest_actual':(snapshot.as_of-through).days if through else None},
        'regular_revenue':money(total),'regular_customers':len(profile),
        'monthly':monthly_rows,'customers':profile,'weekday':weekday_rows,'days_of_month':day_rows,
        'year_over_year':changes,
        'analysis_periods': {
            'basis': 'completed_months_with_recorded_regular_invoices',
            'observed_completed_regular_months': len(analysis_months),
            'unknown_regular_months_with_other_invoices': [
                row['month'] for row in completed_months if row['regular_invoices'] == 0],
            'excluded_incomplete_months': [row['month'] for row in monthly_rows
                                           if row['month'] == cutoff_month],
        },
        'serial_correlation':{'latest_contiguous_months':len(values),'lag_1':correlation(values,1),
                              'lag_12':correlation(values,12),'lag_12_pairs':max(0,len(values)-12)},
        'benchmarks':benchmarks,
        'limitations':[
            'Descriptive patterns are not validated future forecasts.',
            'Missing dates/months are not silently imputed as zero. Ledger coverage must be verified before training.',
            'Monthly correlations, year-over-year comparisons and benchmarks use only completed months with regular invoices; a recorded zero is retained. Coverage attestations are not inputs to this invoice-only analysis.',
            'Invoice dates can reflect billing cadence rather than consumption or delivery dates.',
            'Unclassified source entries remain in the ledger and monthly audit, but are excluded from regular/feeding benchmarks.',
            'Research benchmarks use recorded monthly totals; historical coverage is not yet certified.',
            'Historical extraction includes later ledger corrections; archived real-time studies are needed for prospective evidence.',
            'The target is strictly below 15% monthly WAPE for regular revenue; no result is promised or published.',
            'Customers are not dropped for being inactive today; history reflects purchases in the selected snapshot.',
            'Cadence/lapse descriptions are relative to the last observed sale date, not proof of current customer inactivity.',
        ],
    }
