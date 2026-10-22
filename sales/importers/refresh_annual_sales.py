"""Audited dairy-only refresh of imported history, preserving live records."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path

from django.db import transaction

from accounts.models import Users
from config.data_cache import invalidate_data_cache
from inventory.models import Product, ProductBatch
from inventory.services.product_consolidation import product_name_key
from sales.models import Customer, Order, OrderItem, Transaction, TransactionItem
from .annual_sales import CENT, canonical_customer, canonical_product, customer_key
from .load_annual_sales import sale_timestamp

POLICY_PATH = Path(__file__).with_name('dairy_policy.json')
ZERO = Decimal('0.00')


def product_key(label):
    return product_name_key(canonical_product(label))


@dataclass
class RefreshedTicket:
    source: object
    lines: list
    amount: Decimal
    segment: str
    flags: list


def build_plan(parsed, policy=None):
    policy = policy or json.loads(POLICY_PATH.read_text())
    if len(parsed.monthly_checks) != 36 or any(abs(Decimal(c['difference'])) > Decimal('0.05') for c in parsed.monthly_checks):
        raise ValueError('All 36 source sheet totals must reconcile before filtering.')
    retained, excluded, review, held_lines = [], [], [], []
    months = defaultdict(lambda: {'source_revenue': ZERO, 'dairy_revenue': ZERO,
                                  'excluded_revenue': ZERO, 'unclassified_dairy_revenue': ZERO})
    for ticket in parsed.tickets:
        if ticket.cancelled or ticket.duplicate_of or not ticket.lines:
            continue
        if ticket.sale_date is None:
            raise ValueError(f'Missing source date: {ticket.reference}')
        keep = []
        for line in ticket.lines:
            rule = policy['products'].get(product_key(line.product), {
                'decision': 'review', 'reason': 'Unrecognised product: excluded pending review.'})
            if rule['decision'] == 'dairy':
                keep.append(line)
            elif rule['decision'] in ('non_dairy', 'review'):
                row = {'source': ticket.reference, 'row': line.row, 'product': line.product,
                       'quantity': str(line.quantity), 'amount': str(line.amount) if line.amount is not None else None,
                       'decision': rule['decision'], 'reason': rule['reason']}
                excluded.append(row)
                if rule['decision'] == 'review':
                    review.append(row)
            else:
                raise ValueError(f'Invalid product policy: {line.product}')
        # Invoice-only amounts cannot be allocated to products or categories by
        # guessing prices. Hold the entire invoice in the audit for review.
        unresolved = any(line.amount is None for line in ticket.lines)
        if unresolved and keep and len(keep) != len(ticket.lines):
            review.append({'source': ticket.reference, 'decision': 'invoice_hold',
                           'reason': 'Invoice-only total spans retained and excluded products; no defensible allocation.',
                           'amount': str(ticket.amount)})
            held_lines.extend({'source': ticket.reference, 'row': line.row, 'product': line.product,
                               'quantity': str(line.quantity), 'amount': None,
                               'reason': 'Mixed invoice has no individual line amounts.'} for line in keep)
            keep = []
        gross = sum((line.amount or ZERO for line in ticket.lines), ZERO)
        kept_gross = sum((line.amount or ZERO for line in keep), ZERO)
        adjustment = sum(ticket.adjustments, ZERO)
        if keep and len(keep) == len(ticket.lines):
            amount = ticket.amount
        elif keep:
            if gross <= 0 and adjustment:
                raise ValueError(f'Cannot allocate adjustment: {ticket.reference}')
            amount = kept_gross + ((adjustment * kept_gross / gross).quantize(CENT, rounding=ROUND_HALF_UP) if gross else ZERO)
        else:
            amount = ZERO
        month = months[f'{ticket.workbook}:{ticket.sheet}']
        month['source_revenue'] += ticket.amount
        month['dairy_revenue'] += amount
        month['excluded_revenue'] += ticket.amount - amount
        if not keep:
            continue
        flags = list(ticket.quality_flags)
        if not customer_key(ticket.customer_label):
            flags.append('missing_customer')
        if unresolved:
            flags.append('unallocated_invoice_total')
        if any(line.unit_price is None for line in keep):
            flags.append('missing_unit_price')
        flags = sorted(set(flags))
        segment = ('unclassified' if flags else
                   'feeding' if 'feedin' in ticket.customer_label.casefold() else 'regular')
        if segment == 'unclassified':
            month['unclassified_dairy_revenue'] += amount
        retained.append(RefreshedTicket(ticket, keep, amount, segment, flags))
    audit = {
        'policy_version': policy['version'],
        'policy_sha256': sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest(),
        'source_summary': parsed.summary(), 'source_issues': parsed.issues,
        'source_invoice_checks': parsed.daily_checks, 'source_monthly_checks': parsed.monthly_checks,
        'summary': {'transactions': len(retained), 'line_items': sum(len(t.lines) for t in retained),
                    'segments': dict(Counter(t.segment for t in retained)), 'excluded_lines': len(excluded),
                    'held_dairy_lines': len(held_lines),
                    'review_rows': len(review), 'source_revenue': str(sum((m['source_revenue'] for m in months.values()), ZERO)),
                    'dairy_revenue': str(sum((t.amount for t in retained), ZERO)),
                    'excluded_revenue': str(sum((m['excluded_revenue'] for m in months.values()), ZERO))},
        'monthly': [{'source_sheet': k, **{f: str(v) for f, v in m.items()}} for k, m in sorted(months.items())],
        'retained': [{'source': t.source.reference, 'date': t.source.sale_date.isoformat(),
                      'customer': t.source.customer_label, 'si_number': t.source.invoice_number or None,
                      'amount': str(t.amount), 'segment': t.segment, 'flags': t.flags} for t in retained],
        'excluded_lines': excluded, 'review': review, 'held_dairy_lines': held_lines,
    }
    return retained, audit


def _line_signature(item):
    return (item.product_batch.product_id, item.quantity, item.unit_price,
            item.source_product_label, item.source_line_total,
            item.product_id_snapshot, item.product_name_snapshot,
            item.category_id_snapshot, item.category_name_snapshot, item.product_variant_snapshot)


@transaction.atomic
def apply_plan(parsed, planned, admin_username):
    """Only source-referenced history is replaced; never deduct current stock."""
    owner = Users.objects.get(username=admin_username, role='admin', is_active=True)
    references = {t.reference for t in parsed.tickets}
    unmatched = Transaction.objects.filter(
        source_reference__regex=r'^(2023|2024|2025):[^:]+:[0-9]+$'
    ).exclude(source_reference__in=references)
    if unmatched.exists():
        raise ValueError('Imported history contains references absent from the supplied sources; refusing partial refresh.')
    existing = {t.source_reference: t for t in Transaction.objects.select_for_update().filter(source_reference__in=references)}
    if any(t.encoded_at is not None or t.encoding_request_id is not None for t in existing.values()):
        raise ValueError('Manually encoded sales overlap workbook references; refusing refresh.')
    ids = [t.pk for t in existing.values()]
    if TransactionItem.objects.filter(transaction_id__in=ids, product_batch__is_historical_reference=False).exists():
        raise ValueError('Workbook sales are linked to live stock; refusing refresh.')
    if Order.objects.filter(transaction_id__in=ids).exclude(status='fulfilled').exists():
        raise ValueError('Workbook orders have operational changes; refusing refresh.')
    products = defaultdict(list)
    for product in Product.objects.select_related('category'):
        products[product_key(product.name)].append(product)
    for entry in planned:
        for line in entry.lines:
            candidates = [p for p in products[product_key(line.product)] if not p.variant]
            if len(candidates) != 1:
                raise ValueError(f'Expected one existing unvaried product for {line.product}; found {len(candidates)}.')
    customers = defaultdict(list)
    for customer in Customer.objects.all():
        customers[customer_key(customer.name)].append(customer)
    batches = {}
    for batch in ProductBatch.objects.filter(is_historical_reference=True).order_by('id'):
        batches.setdefault(batch.product_id, batch)
    old_lines = defaultdict(list)
    for item in TransactionItem.objects.filter(transaction_id__in=ids).select_related('product_batch').order_by('pk'):
        old_lines[item.transaction_id].append(item)
    orders = {o.transaction_id: o for o in Order.objects.filter(transaction_id__in=ids)}
    counts = Counter()
    wanted = {t.source.reference for t in planned}
    removed = [sale.pk for ref, sale in existing.items() if ref not in wanted]
    # Non-dairy-only history disappears from reports; its complete original
    # receipt remains in the verified pre-refresh backup and exclusion audit.
    Order.objects.filter(transaction_id__in=removed).delete()
    TransactionItem.objects.filter(transaction_id__in=removed).delete()
    Transaction.objects.filter(pk__in=removed).delete()
    counts['removed_transactions'] = len(removed)
    new_items, new_order_items = [], []
    for entry in planned:
        ticket = entry.source
        sale = existing.get(ticket.reference)
        customer = None
        key = customer_key(ticket.customer_label)
        if key:
            options = customers[key]
            customer = next((c for c in options if sale and c.pk == sale.customer_id), None)
            if customer is None and len(options) > 1:
                raise ValueError(f'Ambiguous customer identity: {ticket.customer_label}')
            if customer is None:
                customer = options[0] if options else Customer.objects.create(name=canonical_customer(ticket.customer_label), created_by=owner)
                if not options:
                    customers[key].append(customer)
        recorded = sum((l.quantity * l.unit_price for l in entry.lines if l.unit_price is not None), ZERO).quantize(CENT)
        subtotal = max(recorded, entry.amount)
        discount = subtotal - entry.amount
        notes = list(dict.fromkeys(ticket.notes))
        notes.append('Dairy-only source refresh; excluded sweets/non-dairy per product policy.')
        if entry.flags:
            notes.append('Source review required: ' + ', '.join(entry.flags))
        desired = dict(customer_id=customer.pk if customer else None, subtotal=subtotal,
                       discount_type='fixed' if discount else 'none', discount_value=discount,
                       discount_amount=discount, total_amount=entry.amount,
                       source_invoice_number=ticket.invoice_number or None, source_customer_label=ticket.customer_label,
                       source_sales_segment=entry.segment, source_note='; '.join(notes),
                       created_at=sale_timestamp(ticket.sale_date))
        created = sale is None
        if created:
            sale = Transaction.objects.create(handled_by=owner, source_reference=ticket.reference,
                                              payment_method='unknown')
        changed = [field for field, value in desired.items() if getattr(sale, field) != value]
        if changed:
            Transaction.objects.filter(pk=sale.pk).update(**{f: desired[f] for f in changed})
        counts['created_transactions' if created else 'updated_transactions' if changed else 'unchanged_transactions'] += 1
        expected, items = [], []
        for line in entry.lines:
            product = next(p for p in products[product_key(line.product)] if not p.variant)
            if product.pk not in batches:
                batches[product.pk] = ProductBatch.objects.create(
                    product=product, batch_number=f'REFRESH-HIST-{product.pk}', initial_quantity=ZERO,
                    remaining_quantity=ZERO, date_received=ticket.sale_date, expiration_date=ticket.sale_date,
                    status='depleted', is_historical_reference=True)
            item = TransactionItem(transaction=sale, product_batch=batches[product.pk],
                       quantity=line.quantity, unit_price=line.unit_price or ZERO,
                       source_product_label=line.product, source_line_total=line.amount,
                       product_id_snapshot=product.pk, product_name_snapshot=product.name,
                       product_variant_snapshot=product.variant, category_id_snapshot=product.category_id,
                       category_name_snapshot=product.category.name)
            items.append(item); expected.append(_line_signature(item))
        lines_changed = [_line_signature(i) for i in old_lines[sale.pk]] != expected
        if lines_changed:
            TransactionItem.objects.filter(transaction=sale).delete()
            new_items.extend(items)
            counts['replaced_line_sets'] += 1
        order = orders.get(sale.pk)
        if customer is None:
            if order:
                order.delete()
        else:
            order_changed = (order is None or order.customer_id != customer.pk or
                             order.discount_type != desired['discount_type'] or order.discount_value != discount or
                             order.created_at != desired['created_at'])
            if order is None:
                order = Order.objects.create(customer=customer, handled_by=owner, transaction=sale)
            if order_changed:
                Order.objects.filter(pk=order.pk).update(customer=customer, discount_type=desired['discount_type'],
                    discount_value=discount, created_at=desired['created_at'], updated_at=desired['created_at'])
            if lines_changed or order_changed:
                OrderItem.objects.filter(order=order).delete()
                for item, line in zip(items, entry.lines, strict=True):
                    new_order_items.append(OrderItem(order=order, product_id=item.product_id_snapshot,
                        quantity=line.quantity, unit_price=line.unit_price or ZERO, subtotal=line.amount or ZERO))
    TransactionItem.objects.bulk_create(new_items, batch_size=1000)
    OrderItem.objects.bulk_create(new_order_items, batch_size=1000)
    actual = list(Transaction.objects.filter(source_reference__in=wanted).values_list('total_amount', flat=True))
    if len(actual) != len(planned) or sum(actual, ZERO) != sum((t.amount for t in planned), ZERO):
        raise ValueError('Refreshed sales failed revenue/count reconciliation; rolling back.')
    if TransactionItem.objects.filter(transaction__source_reference__in=wanted).count() != sum(len(t.lines) for t in planned):
        raise ValueError('Refreshed item counts do not reconcile; rolling back.')
    transaction.on_commit(invalidate_data_cache)
    return dict(counts)
