from django.db import transaction as db_transaction
from decimal import Decimal
from ..models import Transaction, TransactionItem, Order
from inventory.services.batch_service import BatchService


def checkout(cart_items, staff_user, payment_method='cash',
            discount_type='none', discount_value=Decimal('0.00'),
            amount_tendered=None):
    """
    cart_items: list of (product, quantity) OR (product, quantity, locked_price)
    tuples. When locked_price is supplied (e.g. an Order's snapshotted
    OrderItem.unit_price), it overrides the batch/product's live price, so
    a customer is charged what they were quoted at order time rather than
    whatever the price has drifted to by fulfillment.
    """
    with db_transaction.atomic():
        txn = Transaction.objects.create(
            handled_by=staff_user,
            payment_method=payment_method,
            subtotal=Decimal('0.00'),
            total_amount=Decimal('0.00')
        )
        subtotal = Decimal('0.00')
        for entry in cart_items:
            if len(entry) == 3:
                product, quantity, locked_price = entry
            else:
                product, quantity = entry
                locked_price = None

            consumed = BatchService.deduct_product_batch(product, quantity)
            for batch, qty_taken in consumed:
                if locked_price is not None:
                    price = locked_price
                else:
                    price = batch.unit_price if batch.unit_price is not None else batch.product.unit_price
                TransactionItem.objects.create(
                    transaction=txn,
                    product_batch=batch,
                    quantity=qty_taken,
                    unit_price=price
                )
                subtotal += Decimal(str(qty_taken)) * price
        # --- discount ---
        if discount_type == 'percent':
            if not (0 <= discount_value <= 100):
                raise ValueError("Percentage discount must be between 0 and 100.")
            discount_amount = subtotal * (discount_value / Decimal('100'))
        elif discount_type == 'fixed':
            if discount_value < 0:
                raise ValueError("Discount value cannot be negative.")
            if discount_value > subtotal:
                raise ValueError("Fixed discount cannot exceed the subtotal.")
            discount_amount = discount_value
        elif discount_type == 'none':
            discount_amount = Decimal('0.00')
        else:
            raise ValueError(f"Invalid discount_type '{discount_type}'. Must be one of 'none', 'percent', 'fixed'.")
        total = subtotal - discount_amount
        # --- cash tendered / change ---
        change_due = None
        if payment_method == 'cash':
            if amount_tendered is None:
                raise ValueError("Amount tendered is required for cash payments.")
            if amount_tendered < total:
                raise ValueError(f"Amount tendered ({amount_tendered}) is less than the total due ({total}).")
            change_due = amount_tendered - total
        txn.subtotal = subtotal
        txn.discount_type = discount_type
        txn.discount_value = discount_value
        txn.discount_amount = discount_amount
        txn.total_amount = total
        txn.amount_tendered = amount_tendered
        txn.change_due = change_due
        txn.save()
        return txn


def fulfill_order(order, staff_user, payment_method='cash', amount_tendered=None):
    with db_transaction.atomic():
        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        if locked_order.status != 'confirmed':
            raise ValueError(f"Order must be 'confirmed' to fulfill, currently '{locked_order.status}'.")
        cart_items = [
            (item.product, item.quantity, item.unit_price)
            for item in locked_order.items.all()
        ]
        txn = checkout(
            cart_items, staff_user, payment_method,
            discount_type=locked_order.discount_type,
            discount_value=locked_order.discount_value,
            amount_tendered=amount_tendered
        )
        locked_order.transaction = txn
        locked_order.status = 'fulfilled'
        locked_order.save()
    order.refresh_from_db()
    return txn