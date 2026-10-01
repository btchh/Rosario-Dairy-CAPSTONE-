from datetime import timedelta
from decimal import Decimal
import logging

from django.core.cache import cache
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Min, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from inventory.models import Ingredient, IngredientBatch, Product, ProductBatch
from sales.models import Customer, Transaction, TransactionItem
from systemsetting.runtime import get_runtime_settings


REPORT_TYPES = (
    'daily_sales', 'weekly_sales', 'monthly_sales', 'inventory',
    'sarima_forecast', 'customer',
)
CACHE_PREFIX = 'reporting:preview:v2:'
CACHE_TIMEOUT = 300
logger = logging.getLogger(__name__)


def _money(value):
    return value or Decimal('0.00')


def _growth_rate(current, previous):
    current, previous = _money(current), _money(previous)
    if previous == 0:
        return None if current == 0 else 100.0
    return round(float(((current - previous) / previous) * 100), 2)


def _sales_summary(start_date, end_date):
    values = Transaction.objects.filter(
        is_voided=False,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    ).aggregate(revenue=Sum('total_amount'), transaction_count=Count('id'))
    return _money(values['revenue']), values['transaction_count']


def _product_label(name, variant):
    return f'{name} · {variant}' if variant else name


def _sales_drivers(start_date, end_date):
    transactions = Transaction.objects.filter(
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
    category_mix = list(_period_items(start_date, end_date).annotate(
        category=Coalesce('category_name_snapshot', 'product_batch__product__category__name'),
    ).values('category').annotate(
        gross_sales=Sum(_line_revenue()), quantity=Sum('quantity'),
    ).order_by('-gross_sales')[:8])
    # The source sales workbooks put pack sizes beneath each product heading.
    # Roll up the transaction snapshots by product name so renamed catalog
    # entries do not rewrite the history shown in exported reports.
    family_mix = list(_period_items(start_date, end_date).annotate(
        product_name=Coalesce('product_name_snapshot', 'product_batch__product__name'),
    ).values('product_name').annotate(
        gross_sales=Sum(_line_revenue()), quantity=Sum('quantity'),
    ).order_by('-gross_sales', 'product_name')[:10])
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


def daily_sales():
    today = timezone.localdate()
    revenue, count = _sales_summary(today, today)
    line_total = ExpressionWrapper(
        F('quantity') * F('unit_price'),
        output_field=DecimalField(max_digits=20, decimal_places=2),
    )
    item_rows = (
        TransactionItem.objects.filter(
            transaction__is_voided=False,
            transaction__created_at__date=today,
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
    yesterday = today - timedelta(days=1)
    previous_revenue, previous_count = _sales_summary(yesterday, yesterday)
    return {
        'date': today,
        'product_revenue_basis': 'gross_before_discounts',
        'total_revenue': revenue,
        'transaction_count': count,
        'previous_revenue': previous_revenue,
        'previous_transaction_count': previous_count,
        'growth_rate': _growth_rate(revenue, previous_revenue),
        'items': items,
        **_sales_drivers(today, today),
    }


def _line_revenue():
    return ExpressionWrapper(
        F('quantity') * F('unit_price'),
        output_field=DecimalField(max_digits=20, decimal_places=2),
    )


def _period_items(start_date, end_date):
    return TransactionItem.objects.filter(
        transaction__is_voided=False,
        transaction__created_at__date__gte=start_date,
        transaction__created_at__date__lte=end_date,
    )


def _daily_sales_breakdown(start_date, end_date):
    daily_rows = (
        Transaction.objects.filter(
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


def _top_products(start_date, end_date):
    product_rows = (
        _period_items(start_date, end_date)
        .annotate(sale_product_name=Coalesce('product_name_snapshot', 'product_batch__product__name'))
        .annotate(sale_variant=Coalesce('product_variant_snapshot', 'product_batch__product__variant'))
        .values('sale_product_name', 'sale_variant')
        .annotate(revenue=Sum(_line_revenue()), quantity=Sum('quantity'))
        .order_by('-revenue')[:10]
    )
    return [{
        'product_name': _product_label(row['sale_product_name'], row['sale_variant']),
        'quantity': _money(row['quantity']),
        'revenue': _money(row['revenue']),
    } for row in product_rows]


def weekly_sales():
    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=6)
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=6)
    revenue, count = _sales_summary(start_date, end_date)
    previous_revenue, previous_count = _sales_summary(previous_start, previous_end)
    daily_breakdown = _daily_sales_breakdown(start_date, end_date)
    top_products = _top_products(start_date, end_date)
    return {
        'start_date': start_date, 'end_date': end_date, 'revenue': revenue,
        'product_revenue_basis': 'gross_before_discounts',
        'transaction_count': count, 'previous_revenue': previous_revenue,
        'previous_transaction_count': previous_count,
        'growth_rate': _growth_rate(revenue, previous_revenue),
        'daily_breakdown': daily_breakdown, 'top_products': top_products,
        **_sales_drivers(start_date, end_date),
    }


def monthly_sales():
    today = timezone.localdate()
    start_date = today.replace(day=1)
    end_date = today
    previous_month_end = start_date - timedelta(days=1)
    previous_start = previous_month_end.replace(day=1)
    previous_end = min(previous_month_end, previous_start + timedelta(days=today.day - 1))
    revenue, count = _sales_summary(start_date, end_date)
    previous_revenue, previous_count = _sales_summary(previous_start, previous_end)
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
        bucket_revenue, bucket_count = _sales_summary(bucket_start, bucket_end)
        weekly_breakdown.append({
            'week_start': bucket_start,
            'week_end': bucket_end,
            'transaction_count': bucket_count,
            'revenue': bucket_revenue,
        })
    top_products = _top_products(start_date, end_date)
    return {
        'start_date': start_date, 'end_date': end_date, 'revenue': revenue,
        'product_revenue_basis': 'gross_before_discounts',
        'transaction_count': count, 'previous_revenue': previous_revenue,
        'previous_transaction_count': previous_count,
        'previous_period_start': previous_start, 'previous_period_end': previous_end,
        'growth_rate': _growth_rate(revenue, previous_revenue),
        'weekly_breakdown': weekly_breakdown, 'top_products': top_products,
        **_sales_drivers(start_date, end_date),
    }


def _inventory_rows(model, relation_name, item_type, visible_to_staff=False):
    today = timezone.localdate()
    soon = today + timedelta(days=7)
    rows = model.objects.filter(is_active=True)
    if visible_to_staff and model is Product:
        rows = rows.filter(category__is_visible_to_staff=True)
    rows = rows.annotate(
        stock=Sum(
            f'{relation_name}__remaining_quantity',
            filter=Q(**{f'{relation_name}__status': 'available'}),
        ),
        next_expiration=Min(
            f'{relation_name}__expiration_date',
            filter=Q(**{f'{relation_name}__status': 'available'}),
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
            'low_stock_threshold': Decimal(str(item.low_stock_threshold)),
            'is_low_stock': quantity <= item.low_stock_threshold,
            'next_expiration_date': next_expiration, 'fefo_status': fefo_status,
        })
    return result


def inventory_status(visible_to_staff=False):
    today = timezone.localdate()
    soon = today + timedelta(days=7)
    items = _inventory_rows(Product, 'batches', 'product', visible_to_staff)
    items += _inventory_rows(Ingredient, 'batches', 'ingredient')
    available_batches = Q(status='available', remaining_quantity__gt=0)
    product_batches = ProductBatch.objects.all()
    products = Product.objects.filter(is_active=True)
    if visible_to_staff:
        product_batches = product_batches.filter(product__category__is_visible_to_staff=True)
        products = products.filter(category__is_visible_to_staff=True)
    expired = (
        product_batches.filter(available_batches, expiration_date__lt=today).count()
        + IngredientBatch.objects.filter(available_batches, expiration_date__lt=today).count()
    )
    expiring = (
        product_batches.filter(available_batches, expiration_date__range=(today, soon)).count()
        + IngredientBatch.objects.filter(available_batches, expiration_date__range=(today, soon)).count()
    )
    return {
        'as_of': timezone.now(),
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


def sarima_forecast(period='monthly'):
    """Read an evaluated hybrid snapshot; no model fitting during requests."""
    from forecasting.serving import report
    try:return report(period=period)
    except ValueError as exc:
        return {'generated_at':timezone.now(),'horizon_days':30,'method':'unavailable',
            'is_placeholder':False,'forecast':[],'status':'data_conflict','warnings':[str(exc)]}


def customer_report():
    today = timezone.localdate()
    active_since = today - timedelta(days=89)
    transactions = Transaction.objects.filter(is_voided=False, customer__isnull=False)
    values = transactions.aggregate(
        total_ltv=Sum('total_amount'),
        customers_with_purchases=Count('customer_id', distinct=True),
        active=Count(
            'customer_id', distinct=True,
            filter=Q(created_at__date__gte=active_since),
        ),
    )
    purchased = values['customers_with_purchases']
    total_ltv = _money(values['total_ltv'])
    purchase_counts = transactions.values('customer_id').annotate(n=Count('id'))
    repeat_customers = purchase_counts.filter(n__gte=2).count()
    unassigned = Transaction.objects.filter(is_voided=False, customer__isnull=True).aggregate(
        transaction_count=Count('id'), revenue=Sum('total_amount'),
    )
    top_customers = list(
        transactions
        .values(customer_name=F('customer__name'))
        .annotate(
            transaction_count=Count('id'),
            total_spent=Sum('total_amount'),
        )
        .order_by('-total_spent', 'customer_name')[:10]
    )
    return {
        'as_of': today, 'total_customers': Customer.objects.count(),
        'active_customer_count': values['active'],
        'customers_with_purchases': purchased,
        'average_lifetime_value': (total_ltv / purchased if purchased else Decimal('0.00')),
        'total_lifetime_value': total_ltv,
        'top_customers': top_customers,
        'repeat_customer_count': repeat_customers,
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


def get_report(report_type, force_refresh=False, visible_to_staff=False, period='monthly'):
    if report_type not in REPORT_BUILDERS:
        raise ValueError(f'Unsupported report type: {report_type}')
    if report_type == 'sarima_forecast':
        return sarima_forecast(period)
    scope = 'staff' if visible_to_staff else 'admin'
    settings_version = get_runtime_settings()['version']
    key = f'{CACHE_PREFIX}{settings_version}:{scope}:{report_type}'
    if not force_refresh:
        try:
            cached = cache.get(key)
        except Exception:
            logger.exception('Report cache read failed; generating report directly')
            cached = None
        if cached is not None:
            return cached
    if report_type == 'inventory':
        data = inventory_status(visible_to_staff=visible_to_staff)
    else:
        data = REPORT_BUILDERS[report_type]()
    try:
        cache.set(key, data, CACHE_TIMEOUT)
    except Exception:
        logger.exception('Report cache write failed')
    return data


def refresh_reports(visible_to_staff=False):
    generated_at = timezone.now()
    refreshed = {
        name: get_report(name, force_refresh=True, visible_to_staff=visible_to_staff)
        for name in REPORT_TYPES
    }
    return generated_at, refreshed


def generate_pdf(report_type, data):
    from .pdf import generate_pdf as render_pdf
    return render_pdf(report_type, data)
