"""Actual completed sales snapshots; no forecast code or fitted-model reuse."""
from dataclasses import dataclass
from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
import re

from django.utils import timezone
from sales.models import Transaction, TransactionItem
from sales.importers.refresh_annual_sales import POLICY_PATH, product_key

SOURCE_POLICY = 'actual-dairy-sales-pattern-study-v2'
DEMO_REFERENCE = re.compile(r'^2026:[0-9]{6}$')


@dataclass(frozen=True)
class RecordedSale:
    id: int
    day: date
    customer_key: str
    customer_name: str
    segment: str
    revenue: Decimal


@dataclass(frozen=True)
class SalesSnapshot:
    as_of: date
    revision: str
    sales: tuple[RecordedSale, ...]
    excluded_demo_count: int
    excluded_non_dairy_count: int = 0

    @property
    def daily(self):
        result = {}
        for sale in self.sales:
            result[sale.day] = result.get(sale.day, Decimal('0')) + sale.revenue
        return result


def load_snapshot(as_of=None):
    """as_of is exclusive: today's partial invoices are never training data.

    A historical extraction is corrected ledger history, not proof of what was
    known at that historical date. Saved studies retain the observed revision.
    """
    as_of = as_of or timezone.localdate()
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise ValueError('as_of must be a date')
    return _snapshot(as_of, *_load_source(as_of))


def load_snapshots(cutoffs):
    """Read one ledger source for several exclusive cutoffs in a single request.

    No source survives this call: later requests still see backdated edits,
    cancellations, customer changes and line corrections without cache signals.
    Each cutoff retains its own classification, exclusions and exact revision.
    """
    cutoffs = tuple(cutoffs)
    if any(not isinstance(day, date) or isinstance(day, datetime) for day in cutoffs):
        raise ValueError('as_of must be a date')
    cutoffs = tuple(dict.fromkeys(cutoffs))
    if not cutoffs:
        return {}
    rows, policy, live_lines = _load_source(max(cutoffs))
    result = {}
    for day in cutoffs:
        boundary = timezone.make_aware(datetime.combine(day, time.min))
        selected = [row for row in rows if row['created_at'] < boundary]
        result[day] = _snapshot(day, selected, policy, live_lines)
    return result


def _load_source(as_of):
    cutoff = timezone.make_aware(datetime.combine(as_of, time.min))
    rows = list(Transaction.objects.filter(is_voided=False, created_at__lt=cutoff).order_by('pk').values(
        'pk', 'created_at', 'customer_id', 'customer__name',
        'source_customer_label', 'source_reference', 'source_sales_segment', 'total_amount',
    ))
    policy = json.loads(POLICY_PATH.read_text())
    # Refreshed history already contains only approved dairy lines. New manual
    # sales/checkouts must follow the same product policy as the data grows.
    live_ids = [r['pk'] for r in rows if not r['source_sales_segment'] and not DEMO_REFERENCE.fullmatch(r['source_reference'] or '')]
    live_lines = defaultdict(list)
    for item in TransactionItem.objects.filter(transaction_id__in=live_ids).values(
            'transaction_id', 'product_name_snapshot', 'product_batch__product__name',
            'total_amount', 'source_line_total', 'quantity', 'unit_price'):
        live_lines[item['transaction_id']].append(item)
    return rows, policy, live_lines


def _snapshot(as_of, rows, policy, live_lines):
    sales, excluded, excluded_non_dairy = [], 0, []
    for row in rows:
        if DEMO_REFERENCE.fullmatch(row['source_reference'] or ''):
            excluded += 1
            continue
        source_label = row['source_customer_label'].strip()
        customer_name = source_label or row['customer__name'] or 'Walk-in'
        key = (f"customer:{row['customer_id']}" if row['customer_id'] is not None
               else f"label:{' '.join(source_label.casefold().split())}" if source_label else 'walk-in')
        segment = row['source_sales_segment'] or ('feeding' if 'feedin' in customer_name.casefold() else 'regular')
        revenue = row['total_amount']
        if not row['source_sales_segment']:
            lines = live_lines[row['pk']]
            gross, dairy, decisions = Decimal('0'), Decimal('0'), []
            for item in lines:
                label = item['product_name_snapshot'] or item['product_batch__product__name']
                decision = policy['products'].get(product_key(label), {}).get('decision', 'review')
                decisions.append(decision)
                amount = item['total_amount']
                if amount is None: amount = item['source_line_total']
                if amount is None: amount = item['quantity'] * item['unit_price']
                gross += amount
                if decision == 'dairy': dairy += amount
            if lines and all(d == 'non_dairy' for d in decisions):
                excluded_non_dairy.append(row['pk'])
                continue
            if not lines or 'review' in decisions or gross <= 0:
                segment = 'unclassified'
            else:
                revenue = (revenue * dairy / gross).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        sales.append(RecordedSale(
            id=row['pk'], day=timezone.localtime(row['created_at']).date(),
            customer_key=key, customer_name=customer_name,
            segment=segment, revenue=revenue,
        ))
    # Payment status is intentionally absent: this is revenue, not collections.
    revision = sha256(json.dumps({
        'policy': SOURCE_POLICY, 'timezone': timezone.get_current_timezone_name(),
        'product_policy': sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest(),
        'excluded_non_dairy_ids': excluded_non_dairy,
        'sales': [(s.id, s.day.isoformat(), s.customer_key, s.customer_name, s.segment, str(s.revenue)) for s in sales],
    }, sort_keys=True).encode()).hexdigest()
    return SalesSnapshot(as_of, revision, tuple(sales), excluded, len(excluded_non_dairy))
