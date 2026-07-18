from rest_framework import serializers
from .ingredient_serializer import IngredientSerializer


class LowStockIngredientSerializer(serializers.Serializer):
  ingredient = IngredientSerializer()
  remaining_quantity = serializers.DecimalField(max_digits=10, decimal_places=2)