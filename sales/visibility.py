from .models import Transaction


def visible_transactions(visible_to_staff=False, queryset=None):
    """Exclude whole mixed transactions so hidden revenue cannot leak in totals."""
    queryset = Transaction.objects.all() if queryset is None else queryset
    if visible_to_staff:
        queryset = queryset.exclude(
            items__product_batch__product__category__is_visible_to_staff=False,
        ).exclude(items__product_batch__product__category__is_active=False)
    return queryset
