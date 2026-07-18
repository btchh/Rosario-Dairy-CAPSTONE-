from rest_framework import serializers
from ..models import ProductBatch, IngredientBatch, StockCount
from accounts.serializers import UserSerializer
from .productbatch_serializer import ProdBatchSerializer
from .ingredientbatch_serializer import IngBatchSerializer
from .stockadjustment_serializer import StockAdjustmentSerializer


class StockCountSerializer(serializers.ModelSerializer):
  product_batch = ProdBatchSerializer(read_only=True)
  product_batch_id = serializers.PrimaryKeyRelatedField(
    queryset=ProductBatch.objects.all(),
    source='product_batch',
    write_only=True,
    required=False,
    allow_null=True
  )
  ingredient_batch = IngBatchSerializer(read_only=True)
  ingredient_batch_id = serializers.PrimaryKeyRelatedField(
    queryset=IngredientBatch.objects.all(),
    source='ingredient_batch',
    write_only=True,
    required=False,
    allow_null=True
  )
  counted_by = UserSerializer(read_only=True)
  resulting_adjustment = StockAdjustmentSerializer(read_only=True)

  class Meta:
    model = StockCount
    fields = [
      'id', 'product_batch', 'product_batch_id', 'ingredient_batch', 'ingredient_batch_id',
      'expected_quantity', 'counted_quantity', 'variance', 'counted_by', 'count_date',
      'notes', 'resulting_adjustment', 'created_at'
    ]
    read_only_fields = ['id', 'expected_quantity', 'variance', 'counted_by', 'resulting_adjustment', 'created_at']