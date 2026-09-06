from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from django.db.models import Case, CharField, DecimalField, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from ..models import Product, Ingredient, ProductBatch, IngredientBatch, FEFOConf


STOCK_OUTPUT_FIELD = DecimalField(max_digits=20, decimal_places=2)


def _available_stock(relation_name):
  return Coalesce(
    Sum(
      f'{relation_name}__remaining_quantity',
      filter=Q(**{f'{relation_name}__status': 'available'}),
    ),
    Decimal('0.00'),
    output_field=STOCK_OUTPUT_FIELD,
  )


def _expiry_status(critical_expiry_threshold):
  return Case(
    When(expiration_date__lte=critical_expiry_threshold, then=Value('critical')),
    default=Value('near_expiry'),
    output_field=CharField(),
  )


def check_product_stock(visible_to_staff=False):
  low_stock_prods = []
  products = Product.objects.filter(is_active=True).select_related('category').annotate(
    available_stock=_available_stock('batches')
  )
  if visible_to_staff:
    products = products.filter(category__is_visible_to_staff=True)
  config = FEFOConf.get_config()
  for product in products:
    low_stock_threshold = product.low_stock_threshold if product.low_stock_threshold is not None else config.low_stock_threshold
    total_remaining = product.available_stock
    if total_remaining <= low_stock_threshold:
      low_stock_prods.append({'product': product, 'remaining_quantity': total_remaining})
  return low_stock_prods

def check_ingredient_stock():
  low_stock_ings = []
  ingredients = Ingredient.objects.filter(is_active=True).annotate(
    available_stock=_available_stock('batches')
  )
  config = FEFOConf.get_config()
  for ingredient in ingredients:
    low_stock_threshold = ingredient.low_stock_threshold if ingredient.low_stock_threshold is not None else config.low_stock_threshold
    total_remaining = ingredient.available_stock
    if total_remaining <= low_stock_threshold:
      low_stock_ings.append({'ingredient': ingredient, 'remaining_quantity': total_remaining})
  return low_stock_ings

def check_product_expiration(visible_to_staff=False):
  now = timezone.now().date()
  config = FEFOConf.get_config()
  near_expiry_threshold = now + timedelta(days=config.near_expiry_threshold)
  critical_expiry_threshold = now + timedelta(days=config.critical_expiry_threshold)
  batches = ProductBatch.objects.filter(
    status='available', expiration_date__range=(now, near_expiry_threshold)
  ).annotate(expiry_status=_expiry_status(critical_expiry_threshold))
  if visible_to_staff:
    batches = batches.filter(product__category__is_visible_to_staff=True)
  return batches.order_by('expiration_date')

def check_ingredient_expiration():
  now = timezone.now().date()
  config = FEFOConf.get_config()
  near_expiry_threshold = now + timedelta(days=config.near_expiry_threshold)
  critical_expiry_threshold = now + timedelta(days=config.critical_expiry_threshold)
  return IngredientBatch.objects.filter(
    status='available', expiration_date__range=(now, near_expiry_threshold)
  ).annotate(
    expiry_status=_expiry_status(critical_expiry_threshold)
  ).order_by('expiration_date')
