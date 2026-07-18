from rest_framework import serializers
from .product_serializer import ProductSerializer


class LowStockProductSerializer(serializers.Serializer):
  product = ProductSerializer()
  remaining_quantity = serializers.DecimalField(max_digits=10, decimal_places=2)