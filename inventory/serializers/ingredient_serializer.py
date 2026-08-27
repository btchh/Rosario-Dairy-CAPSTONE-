from rest_framework import serializers
from django.db.models import Sum
from ..models import Ingredient


class IngredientSerializer(serializers.ModelSerializer):
  total_stock = serializers.SerializerMethodField()

  def get_total_stock(self, obj):
    return obj.batches.filter(status='available'). aggregate(
      total=Sum('remaining_quantity')
    )['total'] or 0

  class Meta:
    model = Ingredient
    fields = [
      'id', 'name', 'unit', 'unit_price', 'shelf_life', 'ingredient_type','low_stock_threshold', 'is_active', 'created_at', 'updated_at', 'total_stock'
    ]
    read_only_fields = ['id', 'is_active', 'created_at', 'updated_at', 'total_stock']