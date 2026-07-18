from django.db import transaction as db_transaction
from decimal import Decimal
from ..models import Order, OrderItem, Transaction
from inventory.models import ProductBatch


def update_order_item(order, item_id, quantity):
    with db_transaction.atomic():
        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        if locked_order.status != 'placed':
            raise ValueError(f"Items can only be edited while order status is 'placed', currently '{locked_order.status}'.")

        if quantity <= Decimal('0.00'):
            raise ValueError("Quantity must be greater than zero.")

        try:
            item = OrderItem.objects.select_for_update().get(pk=item_id, order=locked_order)
        except (OrderItem.DoesNotExist, ValueError, TypeError):
            raise ValueError("Order item not found on this order.")

        item.quantity = quantity
        item.subtotal = quantity * item.unit_price
        item.save()
        return item


def remove_order_item(order, item_id):
    with db_transaction.atomic():
        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        if locked_order.status != 'placed':
            raise ValueError(f"Items can only be removed while order status is 'placed', currently '{locked_order.status}'.")

        try:
            item = OrderItem.objects.select_for_update().get(pk=item_id, order=locked_order)
        except (OrderItem.DoesNotExist, ValueError, TypeError):
            raise ValueError("Order item not found on this order.")

        if locked_order.items.count() <= 1:
            raise ValueError("Cannot remove the last item on an order — cancel the order instead.")

        item.delete()


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