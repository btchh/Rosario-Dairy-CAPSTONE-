from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from django.db.models import Sum
from ..models import Product, Ingredient, ProductBatch, IngredientBatch, FEFOConf


def check_product_stock(visible_to_staff=False):
  low_stock_prods = []
  products = Product.objects.filter(is_active=True)
  if visible_to_staff:
    products = products.filter(category__is_visible_to_staff=True)
  config = FEFOConf.get_config()
  for product in products:
    low_stock_threshold = product.low_stock_threshold if product.low_stock_threshold is not None else config.low_stock_threshold
    total_remaining = ProductBatch.objects.filter(product=product, status='available').aggregate(total=Sum('remaining_quantity'))['total'] or Decimal('0.00')
    if total_remaining <= low_stock_threshold:
      low_stock_prods.append({'product': product, 'remaining_quantity': total_remaining})
  return low_stock_prods

def check_ingredient_stock():
  low_stock_ings = []
  ingredients = Ingredient.objects.filter(is_active=True)
  config = FEFOConf.get_config()
  for ingredient in ingredients:
    low_stock_threshold = ingredient.low_stock_threshold if ingredient.low_stock_threshold is not None else config.low_stock_threshold
    total_remaining = IngredientBatch.objects.filter(ingredient=ingredient, status='available').aggregate(total=Sum('remaining_quantity'))['total'] or Decimal('0.00')
    if total_remaining <= low_stock_threshold:
      low_stock_ings.append({'ingredient': ingredient, 'remaining_quantity': total_remaining})
  return low_stock_ings

def check_product_expiration(visible_to_staff=False):
  now = timezone.now().date()
  config = FEFOConf.get_config()
  near_expiry_threshold = now + timedelta(days=config.near_expiry_threshold)
  batches = ProductBatch.objects.filter(
    status='available', expiration_date__lte=near_expiry_threshold
  )
  if visible_to_staff:
    batches = batches.filter(product__category__is_visible_to_staff=True)
  return batches.order_by('expiration_date')

def check_ingredient_expiration():
  now = timezone.now().date()
  config = FEFOConf.get_config()
  near_expiry_threshold = now + timedelta(days=config.near_expiry_threshold)
  return IngredientBatch.objects.filter(
    status='available', expiration_date__lte=near_expiry_threshold
  ).order_by('expiration_date')
