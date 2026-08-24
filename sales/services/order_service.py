from django.db import transaction as db_transaction
from decimal import Decimal
from ..models import Order, OrderItem, Transaction
from inventory.models import ProductBatch
from . import checkout_service


def place_order(customer, items, handled_by, discount_type='none', discount_value=None,
                 payment_method='cash', amount_tendered=None):
    """
    Creates an Order together with its OrderItems and immediately fulfills it.
    Orders are born fulfilled — there's no 'placed'/'confirmed' holding state
    anymore. `items` is a list of (product, quantity) tuples, already
    resolved and validated by the caller (mirrors how checkout_service.checkout()
    takes pre-resolved cart_items). Entirely atomic: any failure — bad stock,
    bad tender — rolls back the Order and every OrderItem with it.
    """
    if discount_value is None:
        discount_value = Decimal('0.00')

    with db_transaction.atomic():
        order = Order.objects.create(
            customer=customer,
            handled_by=handled_by,
            discount_type=discount_type,
            discount_value=discount_value,
        )

        order_items = []
        cart_items = []
        for product, quantity in items:
            unit_price = product.unit_price
            order_items.append(OrderItem(
                order=order,
                product=product,
                quantity=quantity,
                unit_price=unit_price,
                subtotal=quantity * unit_price,
            ))
            # Lock the transaction's price to what was just snapshotted onto
            # the OrderItem, so what's shown on the order matches exactly
            # what the customer is charged.
            cart_items.append((product, quantity, unit_price))
        OrderItem.objects.bulk_create(order_items)

        txn = checkout_service.checkout(
            cart_items, handled_by, payment_method,
            discount_type=discount_type,
            discount_value=discount_value,
            amount_tendered=amount_tendered,
        )

        order.transaction = txn  # pyright: ignore[reportAttributeAccessIssue]
        order.status = 'fulfilled'
        order.save()

    order.refresh_from_db()
    return order


def void_fulfilled_order(order, admin_user):
    if order.transaction is None:
        raise ValueError("This order has no linked transaction to void.")

    with db_transaction.atomic():
        txn = Transaction.objects.select_for_update().get(pk=order.transaction_id)
        if txn.is_voided:
            raise ValueError("This transaction has already been voided.")

        skipped_batches = []
        for item in txn.items.select_related('product_batch').all():
            batch = ProductBatch.objects.select_for_update().get(pk=item.product_batch_id)  # pyright: ignore[reportAttributeAccessIssue]
            if batch.status in ('expired', 'disposed'):
                skipped_batches.append(batch.batch_number)
                continue
            batch.remaining_quantity += item.quantity
            if batch.status == 'depleted':
                batch.status = 'available'
            batch.save()

        txn.is_voided = True
        txn.save()

        order.status = 'cancelled'
        order.save()

    return txn, skipped_batches