from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from contextlib import contextmanager
import logging

from django.core.cache import cache
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Min, Q, Sum
from django.db.models.functions import Coalesce, TruncDate, TruncMonth
from django.db import connection
from rest_framework.exceptions import APIException, PermissionDenied, Throttled
from django.utils import timezone
from config.data_cache import data_version
from sales.visibility import visible_transactions

from inventory.models import FEFOConf, Ingredient, IngredientBatch, Product, ProductBatch, StockAdjustment
from inventory.services.eligibility import expired_stock_filter, expiry_cutoff
from sales.models import Customer, TransactionItem, StockReturn
from systemsetting.runtime import get_runtime_settings


REPORT_TYPES = (
    'daily_sales', 'weekly_sales', 'monthly_sales', 'inventory',
    'sarima_forecast', 'customer',
)
CACHE_PREFIX = 'reporting:preview:v10:'
CACHE_TIMEOUT = 300
logger = logging.getLogger(__name__)


@contextmanager
def report_refresh_slot():
    # PostgreSQL serializes rebuilds across processes; the shared cache enforces
    # a global minimum interval as well as the view's per-account throttle.
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [82197034])
        acquired = cursor.fetchone()[0]
    if not acquired:
        raise Throttled(wait=60, detail='A report refresh is already running.')
    try:
        try:
            allowed = cache.add('reporting:refresh:cooldown:v1', True, timeout=60)
        except Exception as exc:
            error = APIException('Report refresh is temporarily unavailable.')
            error.status_code = 503
            raise error from exc
        if not allowed:
            raise Throttled(wait=60, detail='Reports can be refreshed once per minute.')
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(%s)', [82197034])


def _money(value):
    return value or Decimal('0.00')


def _growth_rate(current, previous):
    current, previous = _money(current), _money(previous)
    if previous == 0:
        return None if current == 0 else 100.0
    return round(float(((current - previous) / previous) * 100), 2)


def _sales_summary(start_date, end_date, visible_to_staff=False):
    values = visible_transactions(visible_to_staff).filter(
        is_voided=False,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    ).aggregate(revenue=Sum('total_amount'), transaction_count=Count('id'))
    return _money(values['revenue']), values['transaction_count']


def _product_label(name, variant):
    return f'{name} · {variant}' if variant else name


def _sales_drivers(start_date, end_date, visible_to_staff=False):
    transactions = visible_transactions(visible_to_staff).filter(
        is_voided=False, created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    )
    totals = transactions.aggregate(
        gross_sales=Sum('subtotal'), discounts=Sum('discount_amount'),
        net_sales=Sum('total_amount'), transaction_count=Count('id'),
    )
    payment_mix = list(transactions.values('payment_method').annotate(
        transaction_count=Count('id'), revenue=Sum('total_amount'),
    ).order_by('-revenue'))
    category_mix = list(_period_items(start_date, end_date, visible_to_staff).annotate(
        category=Coalesce('category_name_snapshot', 'product_batch__product__category__name'),
    ).values('category').annotate(
        gross_sales=Sum(_line_revenue()), quantity=Sum('quantity'),
    ).order_by('-gross_sales', 'category'))
    # The source sales workbooks put pack sizes beneath each product heading.
    # Roll up the transaction snapshots by product name so renamed catalog
    # entries do not rewrite the history shown in exported reports.
    family_mix = list(_period_items(start_date, end_date, visible_to_staff).annotate(
        product_name=Coalesce('product_name_snapshot', 'product_batch__product__name'),
    ).values('product_name').annotate(
        gross_sales=Sum(_line_revenue()), quantity=Sum('quantity'),
    ).order_by('-gross_sales', 'product_name'))
    net = _money(totals['net_sales'])
    count = totals['transaction_count']
    return {
        'gross_sales': _money(totals['gross_sales']),
        'discounts': _money(totals['discounts']),
        'average_ticket': net / count if count else Decimal('0.00'),
        'payment_mix': payment_mix,
        'category_mix': category_mix,
        'family_mix': family_mix,
    }


def daily_sales(visible_to_staff=False, start_date=None, end_date=None):
    today = timezone.localdate()
    start_date, end_date = start_date or today, end_date or today
    revenue, count = _sales_summary(start_date, end_date, visible_to_staff)
    line_total = _line_revenue()
    item_rows = (
        TransactionItem.objects.filter(
            transaction__in=visible_transactions(visible_to_staff),
            transaction__is_voided=False,
            transaction__created_at__date__range=(start_date, end_date),
        )
        .annotate(sale_product_name=Coalesce('product_name_snapshot', 'product_batch__product__name'))
        .annotate(sale_variant=Coalesce('product_variant_snapshot', 'product_batch__product__variant'))
        .values('sale_product_name', 'sale_variant')
        .annotate(
            sold_quantity=Sum('quantity'),
            total_revenue=Sum(line_total),
        )
        .order_by('-total_revenue', 'sale_product_name')
    )
    items = [
        {
            'product_name': _product_label(row['sale_product_name'], row['sale_variant']),
            'quantity': _money(row['sold_quantity']),
            'total_revenue': _money(row['total_revenue']),
        }
        for row in item_rows
    ]
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - (end_date - start_date)
    previous_revenue, previous_count = _sales_summary(previous_start, previous_end, visible_to_staff)
    return {
        'date': end_date, 'start_date': start_date, 'end_date': end_date,
        'previous_period_start': previous_start, 'previous_period_end': previous_end,
        'product_revenue_basis': 'gross_before_discounts',
        'total_revenue': revenue,
        'transaction_count': count,
        'previous_revenue': previous_revenue,
        'previous_transaction_count': previous_count,
        'growth_rate': _growth_rate(revenue, previous_revenue),
        'items': items,
        **_sales_drivers(start_date, end_date, visible_to_staff),
    }


def _line_revenue():
    return Coalesce(
        'subtotal',
        ExpressionWrapper(F('quantity') * F('unit_price'),
                          output_field=DecimalField(max_digits=20, decimal_places=2)),
        output_field=DecimalField(max_digits=20, decimal_places=2),
    )


def _period_items(start_date, end_date, visible_to_staff=False):
    return TransactionItem.objects.filter(
        transaction__in=visible_transactions(visible_to_staff),
        transaction__is_voided=False,
        transaction__created_at__date__gte=start_date,
        transaction__created_at__date__lte=end_date,
    )


def _daily_sales_breakdown(start_date, end_date, visible_to_staff=False):
    daily_rows = (
        visible_transactions(visible_to_staff).filter(
            is_voided=False,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(transaction_count=Count('id'), revenue=Sum('total_amount'))
        .order_by('day')
    )
    daily_by_date = {row['day']: row for row in daily_rows}
    daily_breakdown = []
    date = start_date
    while date <= end_date:
        row = daily_by_date.get(date)
        daily_breakdown.append({
            'date': date,
            'transaction_count': row['transaction_count'] if row else 0,
            'revenue': _money(row['revenue']) if row else Decimal('0.00'),
        })
        date += timedelta(days=1)
    return daily_breakdown


def _top_products(start_date, end_date, visible_to_staff=False):
    product_rows = (
        _period_items(start_date, end_date, visible_to_staff)
        .annotate(sale_product_name=Coalesce('product_name_snapshot', 'product_batch__product__name'))
        .annotate(sale_variant=Coalesce('product_variant_snapshot', 'product_batch__product__variant'))
        .values('sale_product_name', 'sale_variant')
        .annotate(revenue=Sum(_line_revenue()), quantity=Sum('quantity'))
        .order_by('-revenue', 'sale_product_name', 'sale_variant')
    )
    return [{
        'product_name': _product_label(row['sale_product_name'], row['sale_variant']),
        'quantity': _money(row['quantity']),
        'revenue': _money(row['revenue']),
    } for row in product_rows]


def weekly_sales(visible_to_staff=False, start_date=None, end_date=None):
    end_date = end_date or timezone.localdate()
    start_date = start_date or end_date - timedelta(days=6)
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - (end_date - start_date)
    revenue, count = _sales_summary(start_date, end_date, visible_to_staff)
    previous_revenue, previous_count = _sales_summary(previous_start, previous_end, visible_to_staff)
    daily_breakdown = _daily_sales_breakdown(start_date, end_date, visible_to_staff)
    top_products = _top_products(start_date, end_date, visible_to_staff)
    return {
        'start_date': start_date, 'end_date': end_date, 'revenue': revenue,
        'product_revenue_basis': 'gross_before_discounts',
        'transaction_count': count, 'previous_revenue': previous_revenue,
        'previous_transaction_count': previous_count,
        'growth_rate': _growth_rate(revenue, previous_revenue),
        'previous_period_start': previous_start, 'previous_period_end': previous_end,
        'daily_breakdown': daily_breakdown, 'top_products': top_products,
        **_sales_drivers(start_date, end_date, visible_to_staff),
    }


def monthly_sales(visible_to_staff=False, start_date=None, end_date=None):
    today = timezone.localdate()
    custom = start_date is not None
    start_date = start_date or today.replace(day=1)
    end_date = end_date or today
    previous_month_end = start_date - timedelta(days=1)
    previous_start = previous_month_end.replace(day=1)
    previous_end = min(previous_month_end, previous_start + timedelta(days=today.day - 1))
    if custom:
        previous_end = start_date - timedelta(days=1)
        previous_start = previous_end - (end_date - start_date)
    revenue, count = _sales_summary(start_date, end_date, visible_to_staff)
    previous_revenue, previous_count = _sales_summary(previous_start, previous_end, visible_to_staff)
    week_starts = []
    cursor = start_date
    while cursor <= end_date:
        week_start = cursor - timedelta(days=cursor.weekday())
        if not week_starts or week_starts[-1] != week_start:
            week_starts.append(week_start)
        cursor += timedelta(days=1)
    weekly_breakdown = []
    for week_start in week_starts:
        bucket_start = max(week_start, start_date)
        bucket_end = min(week_start + timedelta(days=6), end_date)
        bucket_revenue, bucket_count = _sales_summary(bucket_start, bucket_end, visible_to_staff)
        weekly_breakdown.append({
            'week_start': bucket_start,
            'week_end': bucket_end,
            'transaction_count': bucket_count,
            'revenue': bucket_revenue,
        })
    top_products = _top_products(start_date, end_date, visible_to_staff)
    return {
        'start_date': start_date, 'end_date': end_date, 'revenue': revenue,
        'product_revenue_basis': 'gross_before_discounts',
        'transaction_count': count, 'previous_revenue': previous_revenue,
        'previous_transaction_count': previous_count,
        'previous_period_start': previous_start, 'previous_period_end': previous_end,
        'growth_rate': _growth_rate(revenue, previous_revenue),
        'weekly_breakdown': weekly_breakdown, 'top_products': top_products,
        **_sales_drivers(start_date, end_date, visible_to_staff),
    }


def _inventory_rows(model, relation_name, item_type, visible_to_staff=False):
    today = timezone.localdate()
    config = FEFOConf.get_config()
    soon = expiry_cutoff(today, config.near_expiry_threshold)
    rows = model.objects.filter(is_active=True)
    if visible_to_staff and model is Product:
        rows = rows.filter(category__is_active=True, category__is_visible_to_staff=True)
    rows = rows.annotate(
        stock=Sum(
            f'{relation_name}__remaining_quantity',
            filter=Q(**{
                f'{relation_name}__status': 'available',
                f'{relation_name}__expiration_date__gte': today,
                f'{relation_name}__date_received__lte': today,
            }),
        ),
        next_expiration=Min(
            f'{relation_name}__expiration_date',
            filter=Q(**{
                f'{relation_name}__status': 'available',
                f'{relation_name}__expiration_date__gte': today,
                f'{relation_name}__date_received__lte': today,
                f'{relation_name}__remaining_quantity__gt': 0,
            }),
        ),
    )
    result = []
    for item in rows:
        quantity = _money(item.stock)
        next_expiration = item.next_expiration
        if quantity <= 0 or next_expiration is None:
            fefo_status = 'no_stock'
        elif next_expiration < today:
            fefo_status = 'expired'
        elif next_expiration <= soon:
            fefo_status = 'expiring_soon'
        else:
            fefo_status = 'healthy'
        result.append({
            'item_type': item_type, 'id': item.pk,
            'name': _product_label(item.name, getattr(item, 'variant', None)),
            'unit': item.unit, 'quantity': quantity,
            'low_stock_threshold': Decimal(str(config.low_stock_threshold)),
            'is_low_stock': quantity <= config.low_stock_threshold,
            'next_expiration_date': next_expiration, 'fefo_status': fefo_status,
        })
    return result


def inventory_ledger(visible_to_staff=False, start_date=None, end_date=None):
    """Physical product movements for an inclusive business-date range.

    Encoding deducts stock at entry time even when the invoice is backdated.
    Opening stock is reconstructed, not a persisted opening count.
    """
    custom = start_date is not None
    end_date = end_date or timezone.localdate()
    start_date = start_date or end_date
    products = Product.objects.filter(is_active=True).order_by('name', 'variant', 'pk')
    if visible_to_staff:
        products = products.filter(category__is_active=True, category__is_visible_to_staff=True)
    rows = {p.pk: {
        'id': p.pk, 'name': _product_label(p.name, p.variant), 'unit': p.unit,
        'beginning': Decimal('0'), 'received': Decimal('0'), 'total': Decimal('0'),
        'outlet': Decimal('0'), 'allocations': {}, 'ending': Decimal('0'),
        'adjustments': Decimal('0'), 'remarks': [],
    } for p in products}
    batches = ProductBatch.objects.filter(product_id__in=rows, is_historical_reference=False, date_received__lte=end_date)
    if custom:
        batches = batches.filter(created_at__date__lte=end_date)
    for batch in batches:
        row = rows[batch.product_id]
        row['ending'] += batch.remaining_quantity
        received_on = timezone.localtime(batch.created_at).date()
        if custom:
            received_on = max(received_on, batch.date_received)
        if start_date <= received_on <= end_date:
            row['received'] += batch.initial_quantity
        if not custom and batch.remaining_quantity and (batch.status in ('expired', 'disposed') or batch.expiration_date < end_date):
            row['remarks'].append(f'{batch.remaining_quantity.normalize():f} {batch.status if batch.status in ("expired", "disposed") else "expired"} on hand')
    # Use all physical deductions for visible products, including a visible line
    # in a mixed-category invoice, so the stock equation still reconciles.
    sales = TransactionItem.objects.filter(
        Q(transaction__is_voided=False) | Q(stock_return__isnull=False),
        product_batch__in=batches,
    ).annotate(movement_at=Coalesce('transaction__encoded_at', 'transaction__created_at'))
    # Reverse later recorded movements to recover the selected closing balance.
    # Receipts after the range are excluded by the batch query above.
    if custom:
        for movement in sales.filter(movement_at__date__gt=end_date).values('product_batch__product_id').annotate(quantity=Sum('quantity')):
            rows[movement['product_batch__product_id']]['ending'] += movement['quantity']
        for movement in StockAdjustment.objects.filter(product_batch__in=batches, created_at__date__gt=end_date).values('product_batch__product_id').annotate(quantity=Sum('quantity')):
            rows[movement['product_batch__product_id']]['ending'] += movement['quantity']
        for movement in StockReturn.objects.filter(sale_item__product_batch__in=batches, created_at__date__gt=end_date).values('sale_item__product_batch__product_id').annotate(quantity=Sum('quantity')):
            rows[movement['sale_item__product_batch__product_id']]['ending'] -= movement['quantity']
    sales = sales.filter(movement_at__date__range=(start_date, end_date)).select_related('transaction__customer', 'product_batch')
    visible_ids = set(visible_transactions(True).filter(pk__in=sales.values('transaction_id')).values_list('pk', flat=True)) if visible_to_staff else set()
    columns = {}
    for item in sales:
        row = rows[item.product_batch.product_id]
        txn = item.transaction
        # Mixed invoices stay hidden in staff reports, including their buyers.
        if visible_to_staff and txn.pk not in visible_ids:
            key, name = 'restricted', 'Other stock deductions'
        elif txn.customer_id:
            key, name = f'customer-{txn.customer_id}', txn.customer.name
        elif txn.source_customer_label.strip() and txn.source_customer_label.strip().lower() != 'outlet sales':
            key, name = f'source-{txn.source_customer_label.strip()}', txn.source_customer_label.strip()
        else:
            row['outlet'] += item.quantity
            continue
        columns[key] = name
        row['allocations'][key] = row['allocations'].get(key, Decimal('0')) + item.quantity
    for adjustment in StockAdjustment.objects.filter(product_batch__in=batches, created_at__date__range=(start_date, end_date)).select_related('product_batch'):
        row = rows[adjustment.product_batch.product_id]
        row['adjustments'] += adjustment.quantity
        text = f'{adjustment.quantity.normalize():f} {adjustment.get_adjustment_type_display()}'
        if adjustment.reason:
            text += f': {adjustment.reason}'
        row['remarks'].append(text)
    # Returns are signed stock movements on the cancellation date, separate
    # from the original deduction date. Only the quantity actually restored is
    # credited; expired/disposed/capped returns may restore less or nothing.
    for returned in StockReturn.objects.filter(
            sale_item__product_batch__in=batches, created_at__date__range=(start_date, end_date)
            ).select_related('sale_item__product_batch'):
        row = rows[returned.sale_item.product_batch.product_id]
        row['adjustments'] -= returned.quantity
        if returned.quantity:
            row['remarks'].append(f'{returned.quantity.normalize():f} returned from cancelled sale')
    for row in rows.values():
        deductions = row['outlet'] + sum(row['allocations'].values(), Decimal('0')) + row['adjustments']
        row['total'] = row['ending'] + deductions
        row['beginning'] = row['total'] - row['received']
        row['remarks'] = '; '.join(row['remarks'])
    return {
        'date': end_date, 'start_date': start_date, 'end_date': end_date,
        'columns': [{'key': key, 'name': name} for key, name in sorted(columns.items(), key=lambda entry: (entry[1].casefold(), entry[0]))],
        'rows': list(rows.values()),
    }


def inventory_status(visible_to_staff=False, start_date=None, end_date=None):
    today = timezone.localdate()
    config = FEFOConf.get_config()
    soon = expiry_cutoff(today, config.near_expiry_threshold)
    items = _inventory_rows(Product, 'batches', 'product', visible_to_staff)
    items += _inventory_rows(Ingredient, 'batches', 'ingredient')
    available_batches = Q(status='available', remaining_quantity__gt=0, date_received__lte=today)
    product_batches = ProductBatch.objects.all()
    products = Product.objects.filter(is_active=True)
    if visible_to_staff:
        product_batches = product_batches.filter(product__is_active=True,
            product__category__is_active=True, product__category__is_visible_to_staff=True)
        products = products.filter(category__is_active=True, category__is_visible_to_staff=True)
    expired = (
        product_batches.filter(expired_stock_filter(today)).count()
        + IngredientBatch.objects.filter(expired_stock_filter(today)).count()
    )
    expiring = (
        product_batches.filter(available_batches, expiration_date__range=(today, soon)).count()
        + IngredientBatch.objects.filter(available_batches, expiration_date__range=(today, soon)).count()
    )
    return {
        'as_of': timezone.now(),
        'start_date': start_date or today, 'end_date': end_date or today,
        'is_custom_range': start_date is not None,
        'ledger': inventory_ledger(visible_to_staff, start_date, end_date),
        'total_products': products.count(),
        'total_ingredients': Ingredient.objects.filter(is_active=True).count(),
        'low_stock_count': sum(item['is_low_stock'] for item in items),
        'expired_batch_count': expired,
        'expiring_soon_batch_count': expiring,
        'items': sorted(items, key=lambda item: (item['fefo_status'], item['name'].lower())),
        'status_counts': {
            status: sum(item['fefo_status'] == status for item in items)
            for status in ('no_stock', 'expired', 'expiring_soon', 'healthy')
        },
    }


def sarima_forecast(period='monthly', start_date=None, end_date=None):
    """Compatibility report key; expose replacement status without fitting."""
    from forecasting.serving import report
    try:
        data = report(period=period)
    except ValueError as exc:
        data = {'generated_at':timezone.now(),'horizon_days':30,'method':'unavailable',
            'is_placeholder':False,'forecast':[],'status':'data_conflict','warnings':[str(exc)]}
    if start_date is None:
        return data
    data = {**data, 'start_date': start_date, 'end_date': end_date, 'is_custom_range': True}
    # Monthly predictions are indivisible estimates. Include overlapping months
    # at their full value, retaining their actual dates rather than prorating.
    def overlaps(point):
        first = date.fromisoformat(str(point['date']))
        last = date.fromisoformat(str(point.get('end_date', point['date'])))
        return first <= end_date and last >= start_date
    data['forecast'] = [point for point in data.get('forecast', []) if overlaps(point)]
    projection = data.get('planning_projection')
    if projection and not overlaps(projection):
        data['planning_projection'] = None
    data['range_note'] = ('Predictions overlapping the selected dates are shown at their full period value; '
                          'monthly estimates are not prorated.')
    if not data['forecast']:
        data['regular'] = {key: value for key, value in data.get('regular', {}).items() if key != 'predicted_revenue'}
        data['range_note'] = 'No approved forecast is available for the selected dates. ' + data['range_note']
        if data.get('status') == 'ready':
            data['regular']['status'] = 'no_forecasts_in_range'
            data.update(status='no_forecasts_in_range', status_message='No approved forecast is available for the selected dates.')
    return data


def customer_report(visible_to_staff=False, start_date=None, end_date=None):
    today = timezone.localdate()
    start_date = start_date or today.replace(day=1)
    end_date = end_date or today
    active_since = today - timedelta(days=89)
    history = visible_transactions(visible_to_staff).filter(is_voided=False, created_at__date__lte=today)
    all_sales = history.filter(created_at__date__range=(start_date, end_date))
    transactions = all_sales.filter(customer__isnull=False)
    # Group by identity: unrelated customers can share the same name.
    aggregates = {row['customer_id']: row for row in transactions.values('customer_id').annotate(
        transaction_count=Count('id'), total_spent=Sum('total_amount'),
        paid_amount=Sum('total_amount', filter=Q(payment_status='paid')),
        pending_amount=Sum('total_amount', filter=Q(payment_status='pending')),
        unknown_amount=Sum('total_amount', filter=Q(payment_status='unknown')),
        pending_count=Count('id', filter=Q(payment_status='pending')),
        last_purchase=Max('created_at'),
    )}
    accounts = Customer.objects.filter(pk__in=aggregates).order_by('name', 'pk')
    customers = []
    for customer in accounts:
        stats = aggregates.get(customer.pk, {})
        last = stats.get('last_purchase')
        customers.append({
            'customer_id': customer.pk, 'customer_name': customer.name,
            'is_active': customer.is_active,
            'transaction_count': stats.get('transaction_count', 0),
            'total_spent': _money(stats.get('total_spent')),
            'paid_amount': _money(stats.get('paid_amount')),
            'pending_amount': _money(stats.get('pending_amount')),
            'unknown_amount': _money(stats.get('unknown_amount')),
            'pending_count': stats.get('pending_count', 0),
            'last_purchase': timezone.localtime(last).date() if last else None,
        })
    purchased = len(aggregates)
    total = sum((row['total_spent'] for row in customers), Decimal('0'))
    pending = sum((row['pending_amount'] for row in customers), Decimal('0'))
    unassigned = all_sales.filter(customer__isnull=True).aggregate(
        transaction_count=Count('id'), revenue=Sum('total_amount'),
    )
    lifetime = history.filter(customer__isnull=False).aggregate(
        revenue=Sum('total_amount'), customers=Count('customer_id', distinct=True),
        recent=Count('customer_id', distinct=True, filter=Q(created_at__date__gte=active_since)),
    )
    monthly_rows = list(transactions.annotate(month=TruncMonth('created_at')).values('month', 'customer_id').annotate(
        transaction_count=Count('id'), total_spent=Sum('total_amount'),
        paid_amount=Sum('total_amount', filter=Q(payment_status='paid')),
        pending_amount=Sum('total_amount', filter=Q(payment_status='pending')),
        unknown_amount=Sum('total_amount', filter=Q(payment_status='unknown')),
    ))
    by_month = {}
    names = {row['customer_id']: row['customer_name'] for row in customers}
    for row in monthly_rows:
        month = timezone.localtime(row.pop('month')).date().replace(day=1)
        row['customer_name'] = names[row['customer_id']]
        for field in ('total_spent', 'paid_amount', 'pending_amount', 'unknown_amount'):
            row[field] = _money(row[field])
        by_month.setdefault(month, []).append(row)
    monthly_summary = []
    month = start_date.replace(day=1)
    while month <= end_date:
        month_end = month.replace(day=monthrange(month.year, month.month)[1])
        entries = sorted(by_month.get(month, []), key=lambda row: (row['customer_name'].casefold(), row['customer_id']))
        monthly_summary.append({
            'month': month.strftime('%Y-%m'), 'start_date': max(month, start_date),
            'end_date': min(month_end, end_date), 'customer_count': len(entries),
            'transaction_count': sum(row['transaction_count'] for row in entries),
            'revenue': sum((row['total_spent'] for row in entries), Decimal('0')),
            'customers': entries,
        })
        if month_end >= end_date:
            break
        month = month_end + timedelta(days=1)
    return {
        'as_of': today, 'start_date': start_date, 'end_date': end_date,
        'timezone': timezone.get_current_timezone_name(),
        'total_revenue': total,
        'transaction_count': sum(row['transaction_count'] for row in customers),
        'monthly_summary': monthly_summary,
        'total_customers': Customer.objects.filter(is_active=True).count(),
        'customer_record_count': len(customers),
        'active_customer_count': lifetime['recent'],
        'customers_with_purchases': purchased,
        'average_lifetime_value': _money(lifetime['revenue']) / lifetime['customers'] if lifetime['customers'] else Decimal('0'),
        'total_lifetime_value': _money(lifetime['revenue']),
        'top_customers': sorted((row for row in customers if row['transaction_count']), key=lambda row: (-row['total_spent'], row['customer_name'], row['customer_id'])),
        'customers': customers,
        'repeat_customer_count': sum(row['transaction_count'] >= 2 for row in customers),
        'paid_amount': sum((row['paid_amount'] for row in customers), Decimal('0')),
        'pending_amount': pending,
        'unknown_amount': sum((row['unknown_amount'] for row in customers), Decimal('0')),
        'pending_customer_count': sum(row['pending_count'] > 0 for row in customers),
        'unassigned_transaction_count': unassigned['transaction_count'],
        'unassigned_revenue': _money(unassigned['revenue']),
    }


REPORT_BUILDERS = {
    'daily_sales': daily_sales,
    'weekly_sales': weekly_sales,
    'monthly_sales': monthly_sales,
    'inventory': inventory_status,
    'sarima_forecast': sarima_forecast,
    'customer': customer_report,
}


def get_report(report_type, force_refresh=False, visible_to_staff=False, period='monthly', start_date=None, end_date=None):
    if report_type not in REPORT_BUILDERS:
        raise ValueError(f'Unsupported report type: {report_type}')
    if report_type == 'sarima_forecast' and visible_to_staff:
        raise PermissionDenied('Global sales forecasts require an admin account.')
    scope = 'staff' if visible_to_staff else 'admin'
    settings_version = get_runtime_settings()['version']
    key = (f'{CACHE_PREFIX}{data_version(visible_to_staff=visible_to_staff)}:{settings_version}:{scope}:{report_type}:'
           f'{period}:{start_date}:{end_date}:{timezone.get_current_timezone_name()}:{timezone.localdate()}')
    if not force_refresh:
        try:
            cached = cache.get(key)
        except Exception:
            logger.exception('Report cache read failed; generating report directly')
            cached = None
        if cached is not None:
            return cached
    if report_type == 'sarima_forecast':
        data = sarima_forecast(period, start_date, end_date)
    else:
        data = REPORT_BUILDERS[report_type](visible_to_staff=visible_to_staff, start_date=start_date, end_date=end_date)
        if report_type in ('daily_sales', 'weekly_sales', 'monthly_sales'):
            data['is_custom_range'] = start_date is not None
    try:
        cache.set(key, data, CACHE_TIMEOUT)
    except Exception:
        logger.exception('Report cache write failed')
    return data


def refresh_reports(visible_to_staff=False):
    generated_at = timezone.now()
    refreshed = {
        name: get_report(name, force_refresh=True, visible_to_staff=visible_to_staff)
        for name in REPORT_TYPES if not (visible_to_staff and name == 'sarima_forecast')
    }
    return generated_at, refreshed


def generate_pdf(report_type, data):
    from .pdf import generate_pdf as render_pdf
    return render_pdf(report_type, data)
