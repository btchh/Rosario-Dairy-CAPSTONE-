# TODO: Sort out imports
# TODO: Refactor each service into the corresponding type (e.g product / ingredient)
from datetime import timedelta
from ..utils import batch_utils
from ..models import Product, Ingredient,ProductBatch, IngredientBatch
from django.utils import timezone
from decimal import Decimal
from django.db.models import Sum # , Count, Avg, Max, Min

# Product Batch

def create_product_batch(product, quantity, expiration_date, date_received=None, notes=""):
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
def check_product_stock():
  low_stock_prods = []
  products = Product.objects.filter(is_active=True)

  for product in products:
    total_remaining = ProductBatch.objects.filter(product=product, status='available').aggregate(total=Sum('remaining_quantity'))['total'] or Decimal('0.00')
    if total_remaining < product.low_stock_threshold:
      low_stock_prods.append({
        'product': product,
        'remaining_quantity': total_remaining
      })

  return low_stock_prods

# Low Stock Check (Ingredients)
def check_ingredient_stock():
  low_stock_ings = []
  ingredients = Ingredient.objects.filter(is_active=True)

  for ingredient in ingredients:
    total_remaining = IngredientBatch.objects.filter(ingredient=ingredient, status='available').aggregate(total=Sum('remaining_quantity'))['total'] or Decimal('0.00')
    if total_remaining < ingredient.low_stock_threshold:
      low_stock_ings.append({
        'ingredient': ingredient,
        'remaining_quantity': total_remaining
      })

  return low_stock_ings

# Expiration Check (Products)
def check_product_expiration():
  now = timezone.now().date()
  expiring_soon_threshold = now + timedelta(days=7)  # Define a threshold for "nearing expiration"
  
  expiring_batches = ProductBatch.objects.filter(
    status='available',
    expiration_date__lte=expiring_soon_threshold
  ).order_by('expiration_date')

  return expiring_batches

# Expiration Check (Ingredients)
def check_ingredient_expiration():
  now = timezone.now().date()
  expiring_soon_threshold = now + timedelta(days=7)  # Define a threshold for "nearing expiration"
  
  expiring_batches = IngredientBatch.objects.filter(
    status='available',
    expiration_date__lte=expiring_soon_threshold
  ).order_by('expiration_date')

  return expiring_batches