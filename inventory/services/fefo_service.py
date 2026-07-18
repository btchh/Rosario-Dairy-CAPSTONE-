from decimal import Decimal
from ..models import ProductBatch, IngredientBatch


def deduct_product_batch(product, quantity):
  quantity = Decimal(str(quantity))
  if quantity <= Decimal('0.00'):
    raise ValueError("Quantity must be greater than zero.")
  batches = list(
    ProductBatch.objects.select_for_update()
    .filter(product=product, status='available')
    .order_by('expiration_date')
  )
  total_available = sum(b.remaining_quantity for b in batches)
  if total_available < quantity:
    raise ValueError("Insufficient Products.")

  consumed = []
  for batch in batches:
    if quantity <= 0:
      break
    take = min(batch.remaining_quantity, quantity)
    batch.remaining_quantity -= take
    if batch.remaining_quantity == Decimal('0.00'):
      batch.status = 'depleted'
    batch.save()
    consumed.append((batch, take))
    quantity -= take

  return consumed
  
def deduct_ingredient_batch(ingredient, quantity):
  quantity = Decimal(str(quantity))
  if quantity <= Decimal('0.00'):
    raise ValueError("Quantity must be greater than zero.")
  batches = list(
    IngredientBatch.objects.select_for_update()
    .filter(ingredient=ingredient, status='available')
    .order_by('expiration_date')
  )
  total_avalable = sum(b.remaining_quantity for b in batches)
  if total_avalable < quantity:
    raise ValueError("Insufficient Ingredients")

  consumed = []
  for batch in batches:
    if quantity <= 0:
      break
    take = min(batch.remaining_quantity, quantity)
    batch.remaining_quantity -= take
    if batch.remaining_quantity == Decimal('0.00'):
      batch.status = 'depleted'
    batch.save()
    consumed.append((batch, take))
    quantity -= take

  return consumed