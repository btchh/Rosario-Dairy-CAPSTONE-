from django.db import transaction as db_transaction
from django.utils import timezone
from decimal import Decimal
from ..models import Order, OrderItem, Transaction, StockReturn
from inventory.models import ProductBatch
from . import checkout_service


def place_order(customer, items, handled_by, discount_type='none', discount_value=None,
                 payment_method='cash', amount_tendered=None, payment_status='paid'):
    """Encode final item prices and immediately fulfill atomically."""
    from .item_pricing import ZERO, price_items, reject_invoice_discount
    reject_invoice_discount(discount_type, ZERO if discount_value is None else discount_value)
    lines = price_items(items, handled_by)
    with db_transaction.atomic():
        order = Order.objects.create(customer=customer, handled_by=handled_by)
        OrderItem.objects.bulk_create([
            OrderItem(order=order, product=line.product, quantity=line.quantity,
                      unit_price=line.unit_price, subtotal=line.subtotal,
                      total_amount=line.total_amount)
            for line in lines
        ])
        txn = checkout_service.checkout(
            items, handled_by, payment_method=payment_method,
            amount_tendered=amount_tendered, customer=customer, payment_status=payment_status,
        )
        order.transaction = txn
        order.save(update_fields=['transaction', 'updated_at'])
    order.refresh_from_db()
    return order


def void_transaction(sale_id, *, voided_by=None, reason=''):
    """Void any local sale and record only stock that is actually restored."""
    with db_transaction.atomic():
        txn = Transaction.objects.select_for_update().get(pk=sale_id)
        if txn.source_reference:
            raise ValueError('Imported historical transactions cannot be cancelled.')
        if txn.is_voided:
            raise ValueError("This transaction has already been voided.")

        skipped_batches = []
        for item in txn.items.select_related('product_batch').order_by(
                'product_batch__product_id', 'product_batch__expiration_date', 'product_batch_id', 'pk'):
            batch = ProductBatch.objects.select_for_update().get(pk=item.product_batch_id)  # pyright: ignore[reportAttributeAccessIssue]
            if (batch.status in ('expired', 'disposed') or
                    batch.expiration_date < timezone.localdate()):
                skipped_batches.append(batch.batch_number)
                StockReturn.objects.create(sale_item=item, quantity=Decimal('0'))
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
            StockReturn.objects.create(sale_item=item, quantity=restore_amount)

        txn.is_voided = True
        txn.voided_at = timezone.now()
        txn.voided_by = voided_by
        txn.void_reason = reason
        txn.save(update_fields=['is_voided', 'voided_at', 'voided_by', 'void_reason'])

        warning = (
            f"Stock could not be restored for the following expired/disposed batches: {', '.join(skipped_batches)}"
            if skipped_batches else ''
        )
        # Lock transaction first on both API paths, so order cancellation and
        # direct transaction cancellation cannot restore the same stock twice.
        for order in Order.objects.select_for_update().filter(transaction=txn):
            order.status = 'cancelled'
            order.warning = warning
            order.save(update_fields=['status', 'warning', 'updated_at'])

    return txn, skipped_batches


def void_fulfilled_order(order, *, voided_by=None, reason=''):
    if order.transaction_id is None:
        raise ValueError('This order has no linked transaction to void.')
    result = void_transaction(order.transaction_id, voided_by=voided_by, reason=reason)
    order.refresh_from_db()
    return result
