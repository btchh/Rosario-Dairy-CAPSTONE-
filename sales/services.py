from django.db import transaction as db_transaction
from decimal import Decimal
from .models import Transaction, TransactionItem, Order
from inventory.services.batch_service import deduct_product_batch
from django.db import transaction as db_transaction
from .models import Transaction
from django.db.models import Sum
from django.db.models.functions import TruncDate, TruncWeek, TruncMonth, TruncYear
from .models import Transaction
from django.db.models import Sum, F

def checkout(cart_items, staff_user, payment_method='cash'):
    """
    cart_items: list of (product, quantity) tuples
    Returns the created Transaction.
    """
    with db_transaction.atomic():
        txn = Transaction.objects.create(
            handled_by=staff_user,
            payment_method=payment_method,
            total_amount=Decimal('0.00')
        )

        total = Decimal('0.00')
        for product, quantity in cart_items:
            consumed = deduct_product_batch(product, quantity)
            for batch, qty_taken in consumed:
                price = batch.unit_price if batch.unit_price is not None else batch.product.unit_price
                TransactionItem.objects.create(
                    transaction=txn,
                    product_batch=batch,
                    quantity=qty_taken,
                    unit_price=price
                )
                total += Decimal(str(qty_taken)) * price

        txn.total_amount = total
        txn.save()
        return txn


def fulfill_order(order, staff_user, payment_method='cash'):
    """
    Routes a confirmed Order through the same checkout logic,
    then links the resulting Transaction back to the Order.
    """
    if order.status != 'confirmed':
        raise ValueError(f"Order must be 'confirmed' to fulfill, currently '{order.status}'.")

    cart_items = [(item.product, item.quantity) for item in order.items.all()]
    txn = checkout(cart_items, staff_user, payment_method)

    order.transaction = txn
    order.status = 'fulfilled'
    order.save()
    return txn

def void_fulfilled_order(order, admin_user):
    if order.transaction is None:
        raise ValueError("This order has no linked transaction to void.")

    txn = order.transaction
    if txn.is_voided:
        raise ValueError("This transaction has already been voided.")

    with db_transaction.atomic():
        for item in txn.items.all():
            batch = item.product_batch
            batch.remaining_quantity += item.quantity
            if batch.status == 'depleted':
                batch.status = 'available'
            batch.save()

        txn.is_voided = True
        txn.save()

        order.status = 'cancelled'
        order.save()

    return txn


PERIOD_TRUNC = {
    'daily': TruncDate,
    'weekly': TruncWeek,
    'monthly': TruncMonth,
    'yearly': TruncYear,
}

def get_revenue_report(period='monthly'):
    trunc_fn = PERIOD_TRUNC.get(period)
    if trunc_fn is None:
        raise ValueError(f"Invalid period '{period}'. Must be one of {list(PERIOD_TRUNC)}.")

    return (
        Transaction.objects
        .filter(is_voided=False)
        .annotate(bucket=trunc_fn('created_at'))
        .values('bucket')
        .annotate(total=Sum('total_amount'))
        .order_by('bucket')
    )

def get_best_sellers(limit=10):
    return (
        TransactionItem.objects
        .filter(transaction__is_voided=False)
        .values(
            product_name=F('product_batch__product__name'),
            product_variant=F('product_batch__product__variant')
        )
        .annotate(total_sold=Sum('quantity'))
        .order_by('-total_sold')[:limit]
    )

def get_sales_by_category():
    return (
        TransactionItem.objects
        .filter(transaction__is_voided=False)
        .values(category_name=F('product_batch__product__category__name'))
        .annotate(total_sold=Sum('quantity'))
        .order_by('-total_sold')
    )