# TODO: Sort out imports
# TODO: Refactor each service into the corresponding type (e.g product / ingredient)
from datetime import timedelta
from ..utils import batch_utils
from ..models import Product, Ingredient,ProductBatch, IngredientBatch, FEFOConf, StockAdjustment, StockCount
from django.utils import timezone
from decimal import Decimal
from django.db.models import Sum # , Count, Avg, Max, Min

# Product Batch

def create_product_batch(product, quantity, expiration_date, date_received=None, notes=""):
  if quantity <= Decimal('0.00'):
    raise ValueError("Batch quantity must be greater than zero.")

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

def create_ingredient_batch(ingredient, quantity, expiration_date, supplier=None, unit_price=None, date_received=None, notes=""):
  if quantity <= Decimal('0.00'):
    raise ValueError("Batch quantity must be greater than zero.")

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
    supplier=supplier,
    unit_price=unit_price,
    date_received=date_received if date_received else now.date(),
    notes=notes
  )

  ingredient_batches.save()
  return ingredient_batches

# Product Deduction
def deduct_product_batch(product, quantity):
  quantity = Decimal(str(quantity))
  batches = list(ProductBatch.objects.filter(product=product,status='available').order_by('expiration_date'))
  total_available = sum (b.remaining_quantity for b in batches)
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
  
# Ingredient Deduction
def deduct_ingredient_batch(ingredient, quantity):
  quantity = Decimal(str(quantity))
  batches = list(IngredientBatch.objects.filter(ingredient=ingredient,status='available').order_by('expiration_date'))
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
  
# Low Stock Check (Products)
def check_product_stock():
  low_stock_prods = []
  products = Product.objects.filter(is_active=True)
  config = FEFOConf.get_config()
  for product in products:
    low_stock_threshold = product.low_stock_threshold or config.low_stock_threshold
    total_remaining = ProductBatch.objects.filter(product=product, status='available').aggregate(total=Sum('remaining_quantity'))['total'] or Decimal('0.00')
    if total_remaining <= low_stock_threshold:
      low_stock_prods.append({
        'product': product,
        'remaining_quantity': total_remaining
      })

  return low_stock_prods

# Low Stock Check (Ingredients)
def check_ingredient_stock():
  low_stock_ings = []
  ingredients = Ingredient.objects.filter(is_active=True)
  config = FEFOConf.get_config()
  for ingredient in ingredients:
    low_stock_threshold = ingredient.low_stock_threshold or config.low_stock_threshold
    total_remaining = IngredientBatch.objects.filter(ingredient=ingredient, status='available').aggregate(total=Sum('remaining_quantity'))['total'] or Decimal('0.00')
    if total_remaining <= low_stock_threshold:
      low_stock_ings.append({
        'ingredient': ingredient,
        'remaining_quantity': total_remaining
      })

  return low_stock_ings

# Expiration Check (Products)
def check_product_expiration():
  now = timezone.now().date()
  config = FEFOConf.get_config()
  near_expiry_threshold = now + timedelta(days=config.near_expiry_threshold)
  expiring_batches = ProductBatch.objects.filter(
    status='available',
    expiration_date__lte=near_expiry_threshold
  ).order_by('expiration_date')

  return expiring_batches

# Expiration Check (Ingredients)
def check_ingredient_expiration():
  now = timezone.now().date()
  config = FEFOConf.get_config()
  near_expiry_threshold = now + timedelta(days=config.near_expiry_threshold)
  expiring_batches = IngredientBatch.objects.filter(
    status='available',
    expiration_date__lte=near_expiry_threshold
  ).order_by('expiration_date')

  return expiring_batches

# Stock adjustment
def create_stock_adjustment(adjustment_type, quantity, unit_cost, adjusted_by, reason="", ingredient_batch=None, product_batch=None):
  if bool(product_batch) == bool(ingredient_batch):
    raise ValueError("Exactly one of product_batch or ingredient_batch must be provided.")

  batch = product_batch or ingredient_batch
  assert batch is not None
  batch.remaining_quantity -= quantity
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

  batch = product_batch or ingredient_batch
  assert batch is not None

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
    adjustment = create_stock_adjustment(
      adjustment_type='correction',
      quantity=-variance,
      unit_cost=batch.unit_price or Decimal('0.00'),
      adjusted_by=counted_by,
      reason=notes or 'Daily stock count variance',
      ingredient_batch=ingredient_batch,
      product_batch=product_batch
    )
    count.resulting_adjustment = adjustment #type: ignore
    count.save()

  return count