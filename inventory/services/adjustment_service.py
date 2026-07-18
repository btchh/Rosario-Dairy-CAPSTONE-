from decimal import Decimal
from django.db import transaction as db_transaction
from ..models import ProductBatch, IngredientBatch, StockAdjustment, StockCount


def create_stock_adjustment(adjustment_type, quantity, unit_cost, adjusted_by, reason="", ingredient_batch=None, product_batch=None):
    if bool(product_batch) == bool(ingredient_batch):
        raise ValueError("Exactly one of product_batch or ingredient_batch must be provided.")

    if adjustment_type != 'correction' and quantity <= Decimal('0.00'):
        raise ValueError("Adjustment quantity must be greater than zero.")

    with db_transaction.atomic():
        if product_batch is not None:
            batch = ProductBatch.objects.select_for_update().get(pk=product_batch.pk)  # type: ignore
        else:
            batch = IngredientBatch.objects.select_for_update().get(pk=ingredient_batch.pk)  # type: ignore

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
        batch.save()

        adjustment = StockAdjustment(
            ingredient_batch=ingredient_batch,
            product_batch=product_batch,
            adjustment_type=adjustment_type,
            quantity=quantity,
            unit_cost=unit_cost,
            adjusted_by=adjusted_by,
            reason=reason
        )
        adjustment.save()
        return adjustment

def reconcile(counted_quantity, counted_by, notes="", ingredient_batch=None, product_batch=None):
  if bool(product_batch) == bool(ingredient_batch):
      raise ValueError("Exactly one of product_batch or ingredient_batch must be provided.")

  with db_transaction.atomic():
      if product_batch is not None:
          batch = ProductBatch.objects.select_for_update().get(pk=product_batch.pk)  # type: ignore
      else:
          batch = IngredientBatch.objects.select_for_update().get(pk=ingredient_batch.pk)  # type: ignore

      expected = batch.remaining_quantity
      variance = counted_quantity - expected

      count = StockCount(
          ingredient_batch=ingredient_batch,
          product_batch=product_batch,
          expected_quantity=expected,
          counted_quantity=counted_quantity,
          variance=variance,
          counted_by=counted_by,
          notes=notes
      )
      count.save()

      if variance != Decimal('0.00'):
          if batch.unit_price is None:
              raise ValueError(
                  f"Cannot record a correction for batch {batch.batch_number}: unit_price is not set. Set a unit price on the batch before reconciling."
              )
          adjustment = create_stock_adjustment(
              adjustment_type='correction',
              quantity=-variance,
              unit_cost=batch.unit_price,
              adjusted_by=counted_by,
              reason=notes or 'Daily stock count variance',
              ingredient_batch=ingredient_batch,
              product_batch=product_batch
          )
          count.resulting_adjustment = adjustment
          count.save()

      return count