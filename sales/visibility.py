from django.db.models import Q
from .models import Order, Transaction


def visible_transactions(visible_to_staff=False, queryset=None, *, staff_user_id=None):
    """Staff history is owned by that staff member and excludes mixed hidden sales."""
    queryset = Transaction.objects.all() if queryset is None else queryset
    if visible_to_staff:
        if staff_user_id is None:
            return queryset.none()
        queryset = queryset.filter(handled_by_id=staff_user_id).exclude(
            items__product_batch__product__category__is_visible_to_staff=False,
        ).exclude(items__product_batch__product__category__is_active=False)
    return queryset


def visible_orders(user, queryset=None):
    queryset = Order.objects.all() if queryset is None else queryset
    if user.role == 'staff':
        queryset = queryset.filter(handled_by=user).filter(
            Q(transaction__isnull=True) | Q(transaction__handled_by=user)
        ).exclude(items__product__category__is_visible_to_staff=False).exclude(
            items__product__category__is_active=False
        ).exclude(transaction__items__product_batch__product__category__is_visible_to_staff=False).exclude(
            transaction__items__product_batch__product__category__is_active=False
        )
    return queryset
