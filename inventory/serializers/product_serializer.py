from decimal import Decimal

from rest_framework import serializers
from django.db.models import Sum
from django.utils import timezone
from ..models import Product, Category
from .category_serializer import CategorySerializer
from .stock_threshold import StockThresholdMixin


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
      status='available', expiration_date__gte=timezone.localdate()
    ).aggregate(
      total=Sum('remaining_quantity')
    )['total'] or 0
  
  class Meta:
    model = Product
    fields = [
      'id', 'name', 'variant', 'unit', 'shelf_life', 'low_stock_threshold', 'is_active', 'category', 'category_id', 'total_stock', 'created_at', 'updated_at'
    ]
    read_only_fields = ['id', 'is_active', 'created_at', 'updated_at', 'total_stock']
