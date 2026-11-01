from decimal import Decimal
from django.db import transaction as db_transaction
from django.utils import timezone
from ..models import ProductBatch, StockAdjustment


def create_stock_adjustment(adjustment_type, quantity, unit_cost, adjusted_by, reason: str | None = "", product_batch=None):
    if adjustment_type not in dict(StockAdjustment.ADJUSTMENT_TYPES):
        raise ValueError('Invalid adjustment_type.')
    if product_batch is None:
        raise ValueError("product_batch must be provided.")

    if adjustment_type != 'correction' and quantity <= Decimal('0.00'):
        raise ValueError("Adjustment quantity must be greater than zero.")

    with db_transaction.atomic():
        batch = ProductBatch.objects.select_for_update().get(pk=product_batch.pk)

        expired = batch.status == 'expired' or batch.expiration_date < timezone.localdate()
        if batch.status == 'disposed' or (expired and adjustment_type not in ('expired', 'spoilage')):
            raise ValueError(
                f"Batch {batch.batch_number} is {batch.status} and cannot be adjusted."
            )

        if batch.date_received > timezone.localdate():
            raise ValueError('Stock cannot be adjusted before its receipt date.')

        if quantity > Decimal('0.00') and quantity > batch.remaining_quantity:
            raise ValueError(
                f"Adjustment quantity ({quantity}) exceeds remaining stock ({batch.remaining_quantity}) for batch {batch.batch_number}."
            )

        batch.remaining_quantity -= quantity
        if batch.remaining_quantity > batch.initial_quantity:
            raise ValueError(
                f"Correction would set remaining stock ({batch.remaining_quantity}) above the batch's initial quantity ({batch.initial_quantity}) for batch {batch.batch_number}."
            )
        if batch.remaining_quantity <= Decimal('0.00'):
            batch.remaining_quantity = Decimal('0.00')
            batch.status = 'disposed' if adjustment_type in ['spoilage', 'expired'] else 'depleted'
        elif adjustment_type == 'correction' and batch.status == 'depleted':
            # A correction can restore stock in a previously depleted batch.
            # Make it eligible for stock totals and FEFO.
            batch.status = 'available'
        batch.save()

        adjustment = StockAdjustment(
            product_batch=batch,
            adjustment_type=adjustment_type,
            quantity=quantity,
            unit_cost=unit_cost,
            adjusted_by=adjusted_by,
            reason=reason
        )
        adjustment.save()
        return adjustment
