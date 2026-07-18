from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from ..models import Product, ProductBatch
from ..utils.batch_utils import generate_batch_number
from .product_serializer import ProductSerializer


class ProdBatchSerializer(serializers.ModelSerializer):
  product = ProductSerializer(read_only=True)
  product_id = serializers.PrimaryKeyRelatedField(
    queryset=Product.objects.all(),
    source='product',
    write_only=True
  )
  quantity = serializers.DecimalField(
    max_digits = 10,
    decimal_places = 2, 
    write_only = True
  )
  unit_price = serializers.DecimalField(
    max_digits=10,
    decimal_places=2,
    required=False,
    allow_null=True
  )
  class Meta:
    model = ProductBatch
    fields = [
      'id', 'product', 'product_id', 'batch_number', 'grade', 'unit_price','initial_quantity', 'remaining_quantity', 'quantity', 'expiration_date', 'date_received', 'status', 'notes', 'created_at', 'updated_at'
    ]
    read_only_fields = ['id', 'batch_number', 'initial_quantity', 'remaining_quantity','created_at', 'updated_at']
    
  def create(self, validated_data):
    with transaction.atomic():
        quantity = validated_data.pop('quantity')
        validated_data['initial_quantity'] = quantity
        validated_data['remaining_quantity'] = quantity
        if validated_data.get('unit_price') is None:
            validated_data['unit_price'] = validated_data['product'].unit_price
        now = timezone.now()
        seq = ProductBatch.objects.select_for_update().filter(
            created_at__year=now.year, created_at__month=now.month
        ).count() + 1
        validated_data['batch_number'] = generate_batch_number('PRD', seq)
        return super().create(validated_data)

  def update(self, instance, validated_data):
    validated_data.pop('quantity', None)
    return super().update(instance, validated_data)