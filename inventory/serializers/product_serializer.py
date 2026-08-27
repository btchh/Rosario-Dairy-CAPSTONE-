from rest_framework import serializers
from django.db.models import Sum
from ..models import Product, Category
from .category_serializer import CategorySerializer


class ProductSerializer(serializers.ModelSerializer):
  category = CategorySerializer(read_only=True)
  category_id = serializers.PrimaryKeyRelatedField(
    queryset=Category.objects.all(),
    source='category',
    write_only=True
  )
  total_stock = serializers.SerializerMethodField()

  def get_total_stock(self, obj):
    return obj.batches.filter(status='available').aggregate(
      total=Sum('remaining_quantity')
    )['total'] or 0
  
  class Meta:
    model = Product
    fields = [
      'id', 'name', 'variant', 'unit', 'unit_price', 'shelf_life', 'low_stock_threshold', 'is_active', 'category', 'category_id', 'total_stock', 'created_at', 'updated_at'
    ]
    read_only_fields = ['id', 'is_active', 'created_at', 'updated_at', 'total_stock']