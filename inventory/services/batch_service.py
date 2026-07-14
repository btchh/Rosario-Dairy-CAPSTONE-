# TODO: Sort out imports
# TODO: Refactor each service into the corresponding type (e.g product / ingredient)
from datetime import timedelta
from django.db import transaction as db_transaction
from ..utils import batch_utils
from ..models import Product, Ingredient,ProductBatch, IngredientBatch, FEFOConf, StockAdjustment, StockCount
from django.utils import timezone
from decimal import Decimal
from django.db.models import Sum # , Count, Avg, Max, Min

class BatchService:
  # @staticmethod
  # def create_product_batch(product, quantity, expiration_date, date_received=None, notes=""):
  #   if quantity <= Decimal('0.00'):
  #     raise ValueError("Batch quantity must be greater than zero.")

  #   now = timezone.now()

  #   sequence = ProductBatch.objects.filter(
  #     created_at__month=now.month, created_at__year=now.year
  #   ).count()  # Count existing batches for the current month and year

  #   batch_number = batch_utils.generate_batch_number("PRD", sequence + 1)  # Generate a new batch number

  #   product_batches = ProductBatch(
  #     product=product,
  #     initial_quantity=quantity,
  #     remaining_quantity=quantity,
  #     batch_number=batch_number,
  #     expiration_date=expiration_date,
  #     date_received=date_received if date_received else now.date(),
  #     notes=notes
  #   )

  #   product_batches.save()
  #   return product_batches

  # @staticmethod
  # def create_ingredient_batch(ingredient, quantity, expiration_date, supplier=None, unit_price=None, date_received=None, notes=""):
  #   if quantity <= Decimal('0.00'):
  #     raise ValueError("Batch quantity must be greater than zero.")

  #   now = timezone.now()

  #   sequence = IngredientBatch.objects.filter(
  #     created_at__month=now.month, created_at__year=now.year
  #   ).count()  # Count existing batches for the current month and year
  #   batch_number = batch_utils.generate_batch_number("ING", sequence + 1)  # Generate a new batch number

  #   ingredient_batches = IngredientBatch(
  #     ingredient=ingredient,
  #     initial_quantity=quantity,
  #     remaining_quantity=quantity,
  #     batch_number=batch_number,
  #     expiration_date=expiration_date,
  #     supplier=supplier,
  #     unit_price=unit_price,
  #     date_received=date_received if date_received else now.date(),
  #     notes=notes
  #   )

  #   ingredient_batches.save()
  #   return ingredient_batches

  @staticmethod
  def deduct_product_batch(product, quantity):
    quantity = Decimal(str(quantity))
    # select_for_update locks these rows for the life of the enclosing
    # atomic() block (provided by SalesService.checkout), so a second
    # concurrent deduction against the same product has to wait until
    # this one commits, and will then see the already-updated quantities.
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
    
  @staticmethod
  def deduct_ingredient_batch(ingredient, quantity):
    quantity = Decimal(str(quantity))
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
    
  @staticmethod
  def check_product_stock():
    low_stock_prods = []
    products = Product.objects.filter(is_active=True)
    config = FEFOConf.get_config()
    for product in products:
      low_stock_threshold = product.low_stock_threshold if product.low_stock_threshold is not None else config.low_stock_threshold
      total_remaining = ProductBatch.objects.filter(product=product, status='available').aggregate(total=Sum('remaining_quantity'))['total'] or Decimal('0.00')
      if total_remaining <= low_stock_threshold:
        low_stock_prods.append({
          'product': product,
          'remaining_quantity': total_remaining
        })

    return low_stock_prods
  
  @staticmethod
  def check_ingredient_stock():
    low_stock_ings = []
    ingredients = Ingredient.objects.filter(is_active=True)
    config = FEFOConf.get_config()
    for ingredient in ingredients:
      low_stock_threshold = ingredient.low_stock_threshold if ingredient.low_stock_threshold is not None else config.low_stock_threshold
      total_remaining = IngredientBatch.objects.filter(ingredient=ingredient, status='available').aggregate(total=Sum('remaining_quantity'))['total'] or Decimal('0.00')
      if total_remaining <= low_stock_threshold:
        low_stock_ings.append({
          'ingredient': ingredient,
          'remaining_quantity': total_remaining
        })

    return low_stock_ings

  @staticmethod
  def check_product_expiration():
    now = timezone.now().date()
    config = FEFOConf.get_config()
    near_expiry_threshold = now + timedelta(days=config.near_expiry_threshold)
    expiring_batches = ProductBatch.objects.filter(
      status='available',
      expiration_date__lte=near_expiry_threshold
    ).order_by('expiration_date')

    return expiring_batches

  @staticmethod
  def check_ingredient_expiration():
    now = timezone.now().date()
    config = FEFOConf.get_config()
    near_expiry_threshold = now + timedelta(days=config.near_expiry_threshold)
    expiring_batches = IngredientBatch.objects.filter(
      status='available',
      expiration_date__lte=near_expiry_threshold
    ).order_by('expiration_date')

    return expiring_batches

  @staticmethod
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

  @staticmethod
  def reconcile(counted_quantity, counted_by, notes="", ingredient_batch=None, product_batch=None):
    if bool(product_batch) == bool(ingredient_batch):
      raise ValueError("Exactly one of product_batch or ingredient_batch must be provided.")

    batch = product_batch or ingredient_batch
    assert batch is not None

    with db_transaction.atomic():
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
        adjustment = BatchService.create_stock_adjustment(
          adjustment_type='correction',
          quantity=-variance,
          unit_cost=batch.unit_price or Decimal('0.00'),
          adjusted_by=counted_by,
          reason=notes or 'Daily stock count variance',
          ingredient_batch=ingredient_batch,
          product_batch=product_batch
        )
        count.resulting_adjustment = adjustment
        count.save()

      return count