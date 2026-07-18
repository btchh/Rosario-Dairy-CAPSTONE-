from rest_framework import serializers
from ..models import ProductBatch, IngredientBatch, StockAdjustment
from accounts.serializers import UserSerializer
from .productbatch_serializer import ProdBatchSerializer
from .ingredientbatch_serializer import IngBatchSerializer


class StockAdjustmentSerializer(serializers.ModelSerializer):
  product_batch = ProdBatchSerializer(read_only=True)
  product_batch_id = serializers.PrimaryKeyRelatedField(    
    queryset=ProductBatch.objects.all(),
    source='product_batch',
    write_only = True,
    required = False,
    allow_null = True
  )
  ingredient_batch = IngBatchSerializer(read_only=True)
  ingredient_batch_id = serializers.PrimaryKeyRelatedField(
    queryset = IngredientBatch.objects.all(),
    source = 'ingredient_batch',
    write_only = True,
    required = False,
    allow_null = True
  )

  adjusted_by = UserSerializer(read_only=True)

  class Meta:
    model = StockAdjustment
    fields = [
      'id', 'product_batch', 'product_batch_id','ingredient_batch', 'ingredient_batch_id','adjustment_type', 'quantity', 'unit_cost', 'reason', 'adjusted_by', 'created_at', 'updated_at'
      ]
    read_only_fields = ['id', 'adjusted_by', 'created_at', 'updated_at']