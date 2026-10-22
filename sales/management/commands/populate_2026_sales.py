"""Populate the designated 2026 demonstration year from customer sales history."""
import calendar
import gzip
import hashlib
import io
import json
import os
import random
from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command
from django.db import connection, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from config.data_cache import invalidate_data_cache
from sales.importers.refresh_annual_sales import POLICY_PATH, product_key
from sales.management.commands.refresh_annual_sales import protected_fingerprints, verify_protected
from sales.models import Order, OrderItem, Transaction, TransactionItem

CENT = Decimal('0.01')
DEMO_REFERENCE = r'^2026:[0-9]{6}$'
HISTORY_REFERENCE = r'^(2023|2024|2025):[^:]+:[0-9]+$'


def demo_sales():
    """The namespace, rather than the calendar year, identifies disposable data."""
    return Transaction.objects.filter(source_reference__regex=DEMO_REFERENCE)


def source_sales():
    return Transaction.objects.filter(
        source_reference__regex=HISTORY_REFERENCE, is_voided=False,
        source_sales_segment__in=('regular', 'feeding'),
        created_at__year__range=(2023, 2025),
    )


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def target_date(source):
    """Keep month-end billing; otherwise keep the nearest matching weekday."""
    last = calendar.monthrange(2026, source.month)[1]
    if source.day == calendar.monthrange(source.year, source.month)[1]:
        return date(2026, source.month, last)
    candidates = [date(2026, source.month, day) for day in range(1, last + 1)
                  if date(2026, source.month, day).weekday() == source.weekday()]
    return min(candidates, key=lambda day: abs(day.day - source.day))


def build_plan(seed):
    rng = random.Random(seed)
    policy = json.loads(POLICY_PATH.read_text())
    annual = defaultdict(dict)
    for row in (source_sales()
                .values('customer_id', 'created_at__year').annotate(revenue=Sum('total_amount'))):
        annual[row['customer_id']][row['created_at__year']] = row['revenue']
    source = list(source_sales().filter(created_at__year=2025)
                  .select_related('customer').prefetch_related('items__product_batch__product__category')
                  .order_by('created_at', 'id'))
    if not source:
        raise CommandError('No classified, refreshed 2025 dairy sales are available.')
    plan = []
    for sale in source:
        if not sale.customer_id:
            raise CommandError(f'Source sale {sale.pk} has no identified customer; review before continuing.')
        if not sale.customer.is_active:
            continue
        history = annual[sale.customer_id]
        changes = [history[year] / history[year - 1] - 1 for year in (2024, 2025)
                   if history.get(year, 0) > 0 and history.get(year - 1, 0) > 0]
        # Dampen past growth and cap it so a new contract does not compound explosively.
        trend = (sum(changes) / len(changes) / 2) if changes else Decimal('0')
        trend = min(Decimal('0.15'), max(Decimal('-0.10'), trend))
        factor = (1 + trend) * Decimal(str(rng.uniform(0.94, 1.06)))
        lines = []
        for item in sorted(sale.items.all(), key=lambda item: item.pk):
            product = item.product_batch.product
            if policy['products'].get(product_key(product.name), {}).get('decision') != 'dairy':
                raise CommandError(f'Source sale {sale.pk} contains a non-dairy/unreviewed product: {product.name}. Refresh source data first.')
            if not item.product_batch.is_historical_reference:
                raise CommandError('Source includes live inventory batches; review before continuing.')
            if item.quantity <= 0 or item.unit_price < 0:
                raise CommandError(f'Unsupported quantity/price on item {item.pk}.')
            # Keep discrete units discrete, and liters at their original precision.
            precision = Decimal('1') if item.quantity == item.quantity.to_integral_value() else CENT
            quantity = max(precision, (item.quantity * factor).quantize(precision, rounding=ROUND_HALF_UP))
            price = item.unit_price
            if price == 0 and item.source_line_total:
                price = money(item.source_line_total / item.quantity)
            lines.append({'source': item, 'quantity': quantity, 'price': price,
                          'gross': money(quantity * price)})
        if not lines:
            raise CommandError(f'Source sale {sale.pk} has no items.')
        subtotal = sum((line['gross'] for line in lines), Decimal('0.00'))
        discount_ratio = sale.discount_amount / sale.subtotal if sale.subtotal else Decimal('0')
        if not 0 <= discount_ratio <= 1:
            raise CommandError(f'Unsupported discount on sale {sale.pk}.')
        discount = money(subtotal * discount_ratio)
        total = subtotal - discount
        # Allocate net line amounts with cumulative rounding; their sum equals the ticket.
        cumulative = Decimal('0')
        allocated = Decimal('0')
        for line in lines:
            cumulative += line['gross']
            net = money(total * cumulative / subtotal) if subtotal else Decimal('0')
            line['net'] = net - allocated
            allocated = net
        day = target_date(timezone.localtime(sale.created_at).date())
        plan.append({'source': sale, 'day': day, 'lines': lines,
                     'subtotal': subtotal, 'discount': discount, 'total': total})
    return plan


def summary(plan):
    months = defaultdict(lambda: {'transactions': 0, 'revenue': Decimal('0')})
    for entry in plan:
        month = months[entry['day'].strftime('%Y-%m')]
        month['transactions'] += 1
        month['revenue'] += entry['total']
    return {'transactions': len(plan), 'items': sum(len(entry['lines']) for entry in plan),
            'customers': len({entry['source'].customer_id for entry in plan}),
            'revenue': sum((entry['total'] for entry in plan), Decimal('0')),
            'monthly': dict(sorted(months.items()))}


def backup_database(directory):
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    backup = directory / 'database-before.json.gz'
    with backup.open('xb') as raw:
        os.chmod(backup, 0o600)
        with gzip.GzipFile(fileobj=raw, mode='wb') as compressed:
            with io.TextIOWrapper(compressed, encoding='utf-8') as output:
                call_command('dumpdata', all=True, stdout=output, verbosity=0)
    # Confirm that the complete compressed stream can be read before touching sales.
    with gzip.open(backup, 'rt', encoding='utf-8') as source:
        if not isinstance(json.load(source), list):
            raise CommandError('Backup validation failed.')
    return backup


def insert_plan(plan):
    sales, items, orders, order_items = [], [], [], []
    for index, entry in enumerate(plan, 1):
        source = entry['source']
        sales.append(Transaction(
            handled_by_id=source.handled_by_id, customer_id=source.customer_id,
            subtotal=entry['subtotal'], total_amount=entry['total'],
            discount_type='fixed' if entry['discount'] else 'none',
            discount_value=entry['discount'], discount_amount=entry['discount'],
            payment_method='unknown', source_reference=f'2026:{index:06d}',
            source_customer_label=source.source_customer_label or source.customer.name,
            source_sales_segment=source.source_sales_segment,
            source_note=f'Demonstration data only; generated from refreshed dairy source {source.source_reference}. Not an actual sale or forecast.',
        ))
    Transaction.objects.bulk_create(sales, batch_size=500)
    for sale, entry in zip(sales, plan, strict=True):
        sale.created_at = timezone.make_aware(datetime.combine(entry['day'], time(12)))
        orders.append(Order(customer_id=sale.customer_id, handled_by_id=sale.handled_by_id,
                            transaction=sale, discount_type=sale.discount_type,
                            discount_value=sale.discount_value))
    Transaction.objects.bulk_update(sales, ['created_at'], batch_size=500)
    Order.objects.bulk_create(orders, batch_size=500)
    for sale, order, entry in zip(sales, orders, plan, strict=True):
        order.created_at = order.updated_at = sale.created_at
        for number, line in enumerate(entry['lines'], 1):
            source = line['source']
            product = source.product_batch.product
            items.append(TransactionItem(
                transaction=sale, product_batch_id=source.product_batch_id,
                quantity=line['quantity'], unit_price=line['price'],
                line_number=number,
                subtotal=line['gross'], total_amount=line['net'],
                discount_type='fixed' if line['gross'] != line['net'] else 'none',
                discount_value=line['gross']-line['net'], discount_amount=line['gross']-line['net'],
                source_line_total=line['net'], source_product_label=product.name,
                product_id_snapshot=product.pk, product_name_snapshot=product.name,
                product_variant_snapshot=product.variant, category_id_snapshot=product.category_id,
                category_name_snapshot=product.category.name,
            ))
            order_items.append(OrderItem(order=order, product=product,
                                         quantity=line['quantity'], unit_price=line['price'],
                                         subtotal=line['gross'], total_amount=line['net'],
                                         discount_type='fixed' if line['gross'] != line['net'] else 'none',
                                         discount_value=line['gross']-line['net'], discount_amount=line['gross']-line['net']))
    TransactionItem.objects.bulk_create(items, batch_size=1000)
    OrderItem.objects.bulk_create(order_items, batch_size=1000)
    Order.objects.bulk_update(orders, ['created_at', 'updated_at'], batch_size=500)
    expected = summary(plan)
    actual = Transaction.objects.filter(pk__in=[sale.pk for sale in sales]).aggregate(
        count=Count('pk'), revenue=Sum('total_amount'))
    if actual != {'count': expected['transactions'], 'revenue': expected['revenue']}:
        raise CommandError('Inserted sales do not reconcile; rolling back.')
    invalidate_data_cache()


def remove_demo():
    """Caller holds write locks and has a verified full database backup."""
    ids = list(demo_sales().values_list('pk', flat=True))
    if demo_sales().filter(encoded_at__isnull=False).exists() or demo_sales().filter(encoding_request_id__isnull=False).exists():
        raise CommandError('Reserved demo references overlap manually encoded sales; refusing replacement.')
    if TransactionItem.objects.filter(transaction_id__in=ids, product_batch__is_historical_reference=False).exists():
        raise CommandError('Existing demo receipts affect live inventory; refusing replacement.')
    if demo_sales().filter(payment_updated_at__isnull=False).exists() or demo_sales().filter(is_voided=True).exists():
        raise CommandError('Existing demo receipts have operational changes; refusing replacement.')
    if Order.objects.filter(transaction_id__in=ids).exclude(status='fulfilled').exists():
        raise CommandError('Existing demo orders have operational changes; refusing replacement.')
    Order.objects.filter(transaction_id__in=ids).delete()
    TransactionItem.objects.filter(transaction_id__in=ids).delete()
    demo_sales().delete()
    return len(ids)


class Command(BaseCommand):
    help = 'Preview or populate all of the designated 2026 demo year using 2025 customer baskets and 2023–2025 trends.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--seed', type=int, default=2026)
        parser.add_argument('--replace-demo', action='store_true', help='Replace only reserved 2026:NNNNNN demo receipts, preserving actual 2026 sales.')

    def handle(self, *args, **options):
        backup = None
        with transaction.atomic():
            # Keep the preview, backup, and insert consistent, including against another invocation.
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '10s'")
                tables = connection.introspection.table_names(cursor)
                cursor.execute('LOCK TABLE ' + ', '.join(connection.ops.quote_name(t) for t in tables)
                               + ' IN SHARE ROW EXCLUSIVE MODE')
            existing = demo_sales().count()
            if existing and not options['replace_demo']:
                raise CommandError('Demo records already exist; use --replace-demo to refresh only that namespace.')
            plan = build_plan(options['seed'])
            if not plan:
                raise CommandError('No eligible active customer baskets are available; refusing to clear demo records.')
            report = summary(plan)
            report.update(seed=options['seed'], applied=options['apply'],
                          replaced_demo_transactions=existing if options['replace_demo'] else 0,
                          method='Classified dairy-only 2025 baskets, seasonal weekday alignment, damped classified 2023–2025 customer trends, 6% quantity variation',
                          product_policy_sha256=hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest())
            if options['apply']:
                directory = Path(settings.BASE_DIR) / 'backups' / timezone.localtime().strftime('populate-2026-%Y%m%d-%H%M%S-%f')
                backup = backup_database(directory)
                refs = list(demo_sales().values_list('source_reference', flat=True))
                protected = protected_fingerprints(refs)
                initial = {**report, 'completed': False, 'protected_before': protected,
                           'backup_sha256': hashlib.sha256(backup.read_bytes()).hexdigest()}
                manifest = directory / 'manifest.json'
                manifest.write_text(json.dumps(initial, indent=2, default=str) + '\n')
                manifest.chmod(0o600)
                if options['replace_demo']:
                    remove_demo()
                insert_plan(plan)
                verify_protected(protected)
                report['backup'] = str(backup)
                # Private manifest provides a record of the generation without changing display labels.
                manifest.write_text(json.dumps({**initial, **report, 'completed': False,
                                               'protected_verified': True}, indent=2, default=str) + '\n')
                (directory / 'source-map.json').write_text(json.dumps([
                    {'reference': f'2026:{index:06d}', 'source_transaction_id': entry['source'].pk,
                     'source_reference': entry['source'].source_reference,
                     'segment': entry['source'].source_sales_segment,
                     'date': str(entry['day'])} for index, entry in enumerate(plan, 1)
                ], indent=2) + '\n')
                (directory / 'source-map.json').chmod(0o600)
                (directory / POLICY_PATH.name).write_bytes(POLICY_PATH.read_bytes())
                (directory / POLICY_PATH.name).chmod(0o600)
        if backup:
            saved = json.loads(manifest.read_text())
            saved.update(completed=True, files={
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in directory.iterdir() if path.is_file() and path.name != 'manifest.json'})
            manifest.write_text(json.dumps(saved, indent=2, default=str) + '\n')
        self.stdout.write(json.dumps(report, indent=2, default=str))
