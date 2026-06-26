from ..utils import batch_utils
from ..models import ProductBatch, IngredientBatch
from django.utils import timezone
from decimal import Decimal

# Product Batch

def create_product_batch(product, quantity, expiration_date, date_received=None, notes=""):
  """
  Create a new batch for a product with the given quantity, expiration date, and optional notes.
  """

  now = timezone.now()

  sequence = ProductBatch.objects.filter(
    created_at__month=now.month, created_at__year=now.year
  ).count()  # Count existing batches for the current month and year

  batch_number = batch_utils.generate_batch_number("PRD", sequence + 1)  # Generate a new batch number

  product_batches = ProductBatch(
    product=product,
    initial_quantity=quantity,
    remaining_quantity=quantity,
    batch_number=batch_number,
    expiration_date=expiration_date,
    date_received=date_received if date_received else now.date(),
    notes=notes
  )

  product_batches.save()
  return product_batches

# Ingredient

def create_ingredient_batch(ingredient, quantity, expiration_date, date_received=None, notes=""):
  """
  Create a new batch for an ingredient with the given quantity, expiration date, and optional notes.
  """

  now = timezone.now()

  sequence = IngredientBatch.objects.filter(
    created_at__month=now.month, created_at__year=now.year
  ).count()  # Count existing batches for the current month and year

  batch_number = batch_utils.generate_batch_number("ING", sequence + 1)  # Generate a new batch number

  ingredient_batches = IngredientBatch(
    ingredient=ingredient,
    initial_quantity=quantity,
    remaining_quantity=quantity,
    batch_number=batch_number,
    expiration_date=expiration_date,
    date_received=date_received if date_received else now.date(),
    notes=notes
  )

  ingredient_batches.save()
  return ingredient_batches

# Product Deduction
def deduct_product_batch(product, quantity):
  """
  Deduct a specified quantity from the available batches of a product, starting with the oldest batch.
  """
  batches = ProductBatch.objects.filter(product=product,status='available').order_by('expiration_date')

  for batch in batches:
    if batch.remaining_quantity < quantity:
      quantity -= batch.remaining_quantity
      batch.remaining_quantity = Decimal('0.00')
      batch.status = 'depleted'
      batch.save()
    else:
      batch.remaining_quantity -= quantity
      if batch.remaining_quantity == Decimal('0.00'):
        batch.status = 'depleted'
      batch.save()
      break
  if quantity > Decimal('0.00'):
    raise ValueError("Insufficient Products.")
  
# Ingredient Deduction
def deduct_ingredient_batch(ingredient, quantity):
  """
  Deduct a specified quantity from the available batches of an ingredient, starting with the oldest batch.
  """
  batches = IngredientBatch.objects.filter(ingredient=ingredient,status='available').order_by('expiration_date')

  for batch in batches:
    if batch.remaining_quantity < quantity:
      quantity -= batch.remaining_quantity
      batch.remaining_quantity = Decimal('0.00')
      batch.status = 'depleted'
      batch.save()
    else:
      batch.remaining_quantity -= quantity
      if batch.remaining_quantity == Decimal('0.00'):
        batch.status = 'depleted'
      batch.save()
      break
  if quantity > Decimal('0.00'):
    raise ValueError("Insufficient Ingredients.")
  
# Low Stock Check (Products)
