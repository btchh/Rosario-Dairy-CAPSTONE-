from decimal import Decimal
from rest_framework import serializers
from django.db import transaction
from ..models import Ingredient, IngredientBatch
from ..services.batch_sequence_service import next_sequence
from ..utils.batch_utils import generate_batch_number
from .ingredient_serializer import IngredientSerializer


class IngBatchSerializer(serializers.ModelSerializer):
  ingredient = IngredientSerializer(read_only=True)
  ingredient_id = serializers.PrimaryKeyRelatedField(
    queryset=Ingredient.objects.all(),
    source='ingredient',
    write_only=True
  ) 
  quantity = serializers.DecimalField(
    max_digits = 10,
    decimal_places = 2, 
    write_only = True,
    min_value = Decimal('0.01'),
  )

  class Meta:
    model = IngredientBatch
    fields = [
      'id', 'ingredient', 'ingredient_id','batch_number', 'supplier', 'unit_price', 'initial_quantity', 'remaining_quantity', 'quantity','expiration_date', 'date_received', 'status', 'notes', 'created_at', 'updated_at'
    ]
    read_only_fields = ['id', 'batch_number', 'initial_quantity', 'remaining_quantity','created_at', 'updated_at']

  def create(self, validated_data):
    with transaction.atomic():
      quantity = validated_data.pop('quantity')
      validated_data['initial_quantity'] = quantity
      validated_data['remaining_quantity'] = quantity
      if validated_data.get('unit_price') is None:
          validated_data['unit_price'] = validated_data['ingredient'].unit_price

      seq = next_sequence('ING')
      validated_data['batch_number'] = generate_batch_number('ING', seq)
      return super().create(validated_data)

  def update(self, instance, validated_data):
    validated_data.pop('quantity', None)
    return super().update(instance, validated_data)