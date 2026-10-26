from decimal import Decimal

from rest_framework import serializers
from django.db.models import Sum
from django.utils import timezone
from ..models import Product, Category
from .category_serializer import CategorySerializer
from .stock_threshold import StockThresholdMixin
from inventory.services.product_consolidation import product_name_key


class ProductSerializer(StockThresholdMixin, serializers.ModelSerializer):
  low_stock_threshold = serializers.SerializerMethodField()
  category = CategorySerializer(read_only=True)
  category_id = serializers.PrimaryKeyRelatedField(
    queryset=Category.objects.filter(is_active=True),
    source='category',
    write_only=True
  )
  total_stock = serializers.SerializerMethodField()

  def validate(self, attrs):
    if 'unit_price' in self.initial_data:
      raise serializers.ValidationError({'unit_price': 'Enter the selling price on each sale or order item instead.'})
    name = attrs.get('name', getattr(self.instance, 'name', ''))
    unit = attrs.get('unit', getattr(self.instance, 'unit', '')).strip().casefold()
    variant = (attrs.get('variant', getattr(self.instance, 'variant', '')) or '').strip().casefold()
    candidates = Product.objects.exclude(pk=getattr(self.instance, 'pk', None))
    for product in candidates.only('id', 'name', 'unit', 'variant'):
      if (product_name_key(name) == product_name_key(product.name)
          and unit == product.unit.strip().casefold()
          and variant == (product.variant or '').strip().casefold()):
        raise serializers.ValidationError({'name': f'This product already exists as {product.name} (ID {product.pk}). Use the existing product.'})
    return super().validate(attrs)

  def get_total_stock(self, obj):
    if hasattr(obj, 'available_stock'):
      return obj.available_stock
    if hasattr(obj, 'available_batches_for_total'):
      return sum(
        (batch.remaining_quantity for batch in obj.available_batches_for_total),
        Decimal('0.00'),
      )
    return obj.batches.filter(
      status='available', expiration_date__gte=timezone.localdate(), date_received__lte=timezone.localdate()
    ).aggregate(
      total=Sum('remaining_quantity')
    )['total'] or 0
  
  class Meta:
    model = Product
    fields = [
      'id', 'name', 'variant', 'unit', 'shelf_life', 'low_stock_threshold', 'is_active', 'category', 'category_id', 'total_stock', 'created_at', 'updated_at'
    ]
    read_only_fields = ['id', 'is_active', 'created_at', 'updated_at', 'total_stock']
