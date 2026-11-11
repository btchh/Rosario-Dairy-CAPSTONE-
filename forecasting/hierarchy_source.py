"""Read-only item quantities and attributable net revenue from the actual ledger."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR

from django.utils import timezone
from inventory.models import Category, Product
from sales.models import TransactionItem
from sales.importers.refresh_annual_sales import POLICY_PATH, product_key
from .data import load_snapshot
from .provisional import digest
import json

ZERO = Decimal('0')
CENT = Decimal('0.01')


def load_source():
    categories = list(Category.objects.order_by('pk').values('id', 'name'))
    snapshot = load_snapshot()  # Excludes today's incomplete invoices and reserved demos.
    policy = json.loads(POLICY_PATH.read_text())
    products = {p.pk: {'id': p.pk, 'name': p.name, 'variant': p.variant or '',
        'unit': p.unit.strip() or 'unit', 'category_id': p.category_id,
        'category_name': p.category.name, 'is_active': p.is_active,
        'shelf_life': p.shelf_life} for p in Product.objects.select_related('category')}
    sales = {s.id: s for s in snapshot.sales if s.segment in ('regular', 'feeding')}
    lines = defaultdict(list)
    for row in TransactionItem.objects.filter(transaction_id__in=sales).order_by('pk').values(
            'pk', 'transaction_id', 'product_id_snapshot', 'product_batch__product_id',
            'product_name_snapshot', 'quantity', 'unit_price', 'total_amount', 'source_line_total'):
        lines[row['transaction_id']].append(row)
    facts, source_rows = [], []
    unresolved = ZERO
    for sale in sales.values():
        dairy = []
        for row in lines[sale.id]:
            product_id = row['product_id_snapshot'] or row['product_batch__product_id']
            product = products.get(product_id)
            if not product:
                continue
            label = row['product_name_snapshot'] or product['name']
            if policy['products'].get(product_key(label), {}).get('decision') != 'dairy':
                continue
            quantity = row['quantity'] if row['quantity'] > 0 else None
            amount = row['total_amount']
            if amount is None:
                amount = row['source_line_total']
            if amount is None and row['unit_price'] > 0 and quantity is not None:
                amount = quantity * row['unit_price']
            if amount is not None and amount < 0:
                amount = None
            dairy.append((product_id, quantity, amount))
        gross = sum((amount for _, _, amount in dairy if amount is not None), ZERO)
        allocatable = bool(dairy) and all(amount is not None for _, _, amount in dairy) and gross > 0
        allocations = []
        if allocatable:
            exact = [sale.revenue * amount / gross for _, _, amount in dairy]
            allocations = [amount.quantize(CENT, rounding=ROUND_FLOOR) for amount in exact]
            pennies = int((sale.revenue-sum(allocations, ZERO))/CENT)
            order = sorted(range(len(dairy)), key=lambda i: (-(exact[i]-allocations[i]), i))
            for index in order[:pennies]:
                allocations[index] += CENT
        for index, (product_id, quantity, amount) in enumerate(dairy):
            revenue = None
            if allocatable:
                revenue = allocations[index]
            facts.append({'day': sale.day, 'segment': sale.segment, 'product_id': product_id,
                          'quantity': quantity, 'revenue': revenue})
        if not allocatable:
            unresolved += sale.revenue
            facts.append({'day': sale.day, 'segment': sale.segment, 'product_id': 0,
                          'quantity': None, 'revenue': sale.revenue})
        source_rows.append((sale.id, sale.day.isoformat(), sale.segment, str(sale.revenue),
                            [(pid, str(q), str(r)) for pid, q, r in dairy]))
    if not sales:
        return {'revision': digest({'sales': [], 'products': products, 'categories': categories}), 'facts': [],
                'products': products, 'categories': categories, 'coverage_days': set(), 'latest_day': None, 'audit': {}}
    latest = max(s.day for s in sales.values())
    earliest = min(s.day for s in sales.values())
    products[0] = {'id': 0, 'name': 'Unallocated invoice revenue', 'variant': '', 'unit': '',
                   'category_id': 0, 'category_name': 'Unallocated revenue',
                   'is_active': True, 'shelf_life': 0}
    return {'revision': digest({'version': 'item-ledger-v1', 'sales': source_rows,
                               'products': products, 'categories': categories, 'policy': policy}),
            'facts': facts, 'products': products, 'categories': categories, 'coverage_days': {s.day for s in sales.values()},
            'latest_day': latest, 'earliest_day': earliest,
            'audit': {'transactions': len(sales), 'quantity_lines': sum(f['quantity'] is not None for f in facts),
                'unallocated_revenue': float(unresolved),
                'excluded_unclassified_transactions': sum(s.segment == 'unclassified' for s in snapshot.sales),
                'excluded_demo_transactions': snapshot.excluded_demo_count,
                'latest_sale': latest.isoformat(), 'first_sale': earliest.isoformat(),
                'days_since_latest_sale': (timezone.localdate()-latest).days,
                'zero_policy': 'Zero denotes no recorded item sales within a period containing eligible ledger sales. Whole periods without eligible ledger sales remain unknown.',
                'revenue_policy': 'Recorded line values weight each invoice net total; the final line receives the rounding remainder. Unallocatable invoices stay in a separate bucket.',
                'quantity_policy': 'Recorded fulfilled purchases; lost demand and non-sales stock consumption are not inferred.',
                'unit_policy': 'Generic unit counts have unverified pack definitions. Litres and other units remain separate.'}}
