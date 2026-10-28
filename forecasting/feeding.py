"""Descriptive feeding evidence; invoice dates are not delivery schedules."""
from collections import defaultdict
from decimal import Decimal
import re
import unicodedata

from .data import load_snapshot
from .learning import shift_month


def program_group(label):
    """Display grouping only; never merge customers or infer contracts."""
    text = unicodedata.normalize('NFKD', label).encode('ascii', 'ignore').decode().casefold()
    words = re.sub(r'[^a-z0-9]+', ' ', text).split()
    if 'dswd' in words:
        return 'DSWD'
    if 'deped' in words:
        return 'DepEd'
    if 'las pinas' in ' '.join(words):
        return 'Las Piñas'
    if 'rizal' in words:
        return 'Rizal'
    return 'Unspecified feeding program'


def investigate_feeding(snapshot):
    sales = sorted((s for s in snapshot.sales if s.segment == 'feeding'), key=lambda s: (s.day, s.id))
    months = defaultdict(list)
    groups = defaultdict(list)
    for sale in sales:
        months[sale.day.replace(day=1)].append(sale)
        groups[program_group(sale.customer_name)].append(sale)
    first = sales[0].day.replace(day=1) if sales else None
    last = sales[-1].day.replace(day=1) if sales else None
    calendar = []
    month = first
    while month is not None and month <= last:
        records = months[month]
        calendar.append({'month': month.isoformat(), 'invoice_count': len(records),
            'recorded_revenue': float(sum((s.revenue for s in records), Decimal('0'))),
            'status': 'feeding_invoice_recorded' if records else 'no_feeding_invoice',
            'absence_of_demand_verified': False})
        month = shift_month(month)
    # Last observed month is not evidence that all later months have zero demand.
    latest_complete = shift_month(snapshot.as_of.replace(day=1), -1)
    since_last = ((latest_complete.year-last.year)*12+latest_complete.month-last.month) if last else None
    return {'status': 'research_only' if sales else 'no_feeding_records', 'scope': 'real',
        'source_revision': snapshot.revision, 'as_of_exclusive': snapshot.as_of.isoformat(),
        'invoice_count': len(sales), 'recorded_revenue': float(sum((s.revenue for s in sales), Decimal('0'))),
        'first_recorded_date': sales[0].day.isoformat() if sales else None,
        'last_recorded_date': sales[-1].day.isoformat() if sales else None,
        'months_with_invoices': sum(bool(r['invoice_count']) for r in calendar),
        'observed_calendar_months': len(calendar),
        'months_without_invoices_within_span': [r['month'] for r in calendar if not r['invoice_count']],
        'complete_months_since_last_record': max(0, since_last) if since_last is not None else None,
        'months': calendar,
        'program_groups': [{'label': label, 'invoice_count': len(records),
            'recorded_revenue': float(sum((s.revenue for s in records), Decimal('0'))),
            'source_labels': sorted({s.customer_name for s in records})}
            for label, records in sorted(groups.items())],
        'readiness': {'production_ready': False, 'status': 'not_validated_for_production',
            'annual_history_review_months': 24,
            'history_review_threshold_met': len(calendar) >= 24,
            'history_threshold_policy': 'Two annual cycles is an evidence-review threshold, not automatic model approval.',
            'reasons': ['Invoice dates describe recorded billing, not confirmed delivery or consumption dates.',
                'Gaps and periods before the first invoice are not verified zero demand.',
                'Production approval requires coverage review and prospective forecast evaluation.']
                + (['Fewer than 24 calendar months since the first recorded feeding invoice.'] if len(calendar) < 24 else [])},
        'warnings': ['Program groups normalize source labels for display only; they do not identify contracts or merge customers.',
            'Only recorded, classified dairy feeding invoices are included; unclassified sales and legacy demo references are excluded. Ordinary local records are included without independent source-authenticity verification.',
            'The first invoice date is not a verified program start. No future contract quantities or schedules are assumed.']}


def feeding_report():
    """Completed months only, using the same actual-data boundary as forecasting."""
    from django.utils import timezone
    return investigate_feeding(load_snapshot(timezone.localdate().replace(day=1)))
