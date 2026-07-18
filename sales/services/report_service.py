from django.db.models import Sum, F
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth, TruncYear
from ..models import Transaction, TransactionItem

PERIOD_TRUNC = {
    'daily': TruncDate,
    'weekly': TruncWeek,
    'monthly': TruncMonth,
    'yearly': TruncYear,
}


def get_revenue_report(period='monthly', start_date=None, end_date=None):
    """
    start_date/end_date are `date` objects (or None) and are inclusive on
    both ends, filtered against the transaction's created_at date.
    """
    trunc_fn = PERIOD_TRUNC.get(period)
    if trunc_fn is None:
        raise ValueError(f"Invalid period '{period}'. Must be one of {list(PERIOD_TRUNC)}.")

    qs = Transaction.objects.filter(is_voided=False)
    if start_date is not None:
        qs = qs.filter(created_at__date__gte=start_date)
    if end_date is not None:
        qs = qs.filter(created_at__date__lte=end_date)

    return (
        qs
        .annotate(bucket=trunc_fn('created_at'))
        .values('bucket')
        .annotate(total=Sum('total_amount'))
        .order_by('bucket')
    )


def get_best_sellers(limit=10, start_date=None, end_date=None):
    qs = TransactionItem.objects.filter(transaction__is_voided=False)
    if start_date is not None:
        qs = qs.filter(transaction__created_at__date__gte=start_date)
    if end_date is not None:
        qs = qs.filter(transaction__created_at__date__lte=end_date)

    return (
        qs
        .values(
            product_name=F('product_batch__product__name'),
            product_variant=F('product_batch__product__variant')
        )
        .annotate(total_sold=Sum('quantity'))
        .order_by('-total_sold')[:limit]
    )


def get_sales_by_category(start_date=None, end_date=None):
    qs = TransactionItem.objects.filter(transaction__is_voided=False)
    if start_date is not None:
        qs = qs.filter(transaction__created_at__date__gte=start_date)
    if end_date is not None:
        qs = qs.filter(transaction__created_at__date__lte=end_date)

    return (
        qs
        .values(category_name=F('product_batch__product__category__name'))
        .annotate(total_sold=Sum('quantity'))
        .order_by('-total_sold')
    )