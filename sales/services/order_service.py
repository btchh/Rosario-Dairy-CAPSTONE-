from django.db import transaction as db_transaction
from django.utils import timezone
from decimal import Decimal
from ..models import Order, OrderItem, Transaction
from inventory.models import ProductBatch
from . import checkout_service


def place_order(customer, items, handled_by, discount_type='none', discount_value=None,
                 payment_method='cash', amount_tendered=None):
    """Encode explicit item prices/discounts and immediately fulfill atomically."""
    from .item_pricing import ZERO, price_items, reject_invoice_discount
    reject_invoice_discount(discount_type, ZERO if discount_value is None else discount_value)
    lines = price_items(items, handled_by)
    with db_transaction.atomic():
        order = Order.objects.create(customer=customer, handled_by=handled_by)
        OrderItem.objects.bulk_create([
            OrderItem(order=order, product=line.product, quantity=line.quantity,
                      unit_price=line.unit_price, subtotal=line.subtotal,
                      discount_type=line.discount_type, discount_value=line.discount_value,
                      discount_amount=line.discount_amount, total_amount=line.total_amount)
            for line in lines
        ])
        txn = checkout_service.checkout(
            items, handled_by, payment_method=payment_method,
            amount_tendered=amount_tendered, customer=customer,
        )
        order.transaction = txn
        order.save(update_fields=['transaction', 'updated_at'])
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
            if (batch.status in ('expired', 'disposed') or
                    batch.expiration_date < timezone.localdate()):
                skipped_batches.append(batch.batch_number)
                continue

            # Cap the restore at initial_quantity, mirroring the cap
            # adjustment_service.create_stock_adjustment() enforces on
            # corrections. Without this, a batch topped up via a
            # correction/reconcile between the sale and the void could end
            # up with remaining_quantity > initial_quantity.
            restorable = batch.initial_quantity - batch.remaining_quantity
            restore_amount = min(item.quantity, restorable) if restorable > Decimal('0.00') else Decimal('0.00')
            if restore_amount > Decimal('0.00'):
                batch.remaining_quantity += restore_amount
                if batch.status == 'depleted':
                    batch.status = 'available'
                batch.save()

        txn.is_voided = True
        txn.save()

        order.status = 'cancelled'
        order.warning = (
            f"Stock could not be restored for the following expired/disposed batches: {', '.join(skipped_batches)}"
            if skipped_batches else ''
        )
        order.save()

    return txn, skipped_batches
