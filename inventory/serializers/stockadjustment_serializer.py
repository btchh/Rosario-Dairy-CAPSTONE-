from rest_framework import serializers
from config.api_inputs import PositivePrimaryKeyRelatedField
from ..models import ProductBatch, StockAdjustment
from accounts.serializers import UserSerializer
from .productbatch_serializer import ProdBatchSerializer


class StockAdjustmentSerializer(serializers.ModelSerializer):
  product_batch = ProdBatchSerializer(read_only=True)
  product_batch_id = PositivePrimaryKeyRelatedField(
    queryset=ProductBatch.objects.all(),
    source='product_batch',
    write_only = True,
    required = True,
    allow_null = False
  )
  adjusted_by = UserSerializer(read_only=True)

  class Meta:
    model = StockAdjustment
    fields = [
      'id', 'product_batch', 'product_batch_id','adjustment_type', 'quantity', 'unit_cost', 'reason', 'adjusted_by', 'created_at', 'updated_at'
      ]
    read_only_fields = ['id', 'adjusted_by', 'created_at', 'updated_at']
