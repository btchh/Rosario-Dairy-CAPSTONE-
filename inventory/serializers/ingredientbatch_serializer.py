from decimal import Decimal
from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from ..models import Ingredient, IngredientBatch
from ..services.batch_sequence_service import next_sequence
from ..utils.batch_utils import generate_batch_number, to_date
from .ingredient_serializer import IngredientSerializer


class IngBatchSerializer(serializers.ModelSerializer):
  ingredient = IngredientSerializer(read_only=True)
  ingredient_id = serializers.PrimaryKeyRelatedField(
    queryset=Ingredient.objects.all(),
    source='ingredient',
    write_only=True
  ) 
  expiry_status = serializers.CharField(read_only=True, required=False)
  quantity = serializers.DecimalField(
    max_digits = 10,
    decimal_places = 2, 
    write_only = True,
    min_value = Decimal('0.01'),
  )

  class Meta:
    model = IngredientBatch
    fields = [
      'id', 'ingredient', 'ingredient_id','batch_number', 'supplier', 'grade', 'unit_price', 'initial_quantity', 'remaining_quantity', 'quantity','expiration_date', 'expiry_status', 'date_received', 'status', 'notes', 'created_at', 'updated_at'
    ]
    read_only_fields = ['id', 'batch_number', 'initial_quantity', 'remaining_quantity', 'status', 'created_at', 'updated_at']

  def validate(self, attrs):
    """
    Rejects an expiration_date earlier than date_received. On partial
    update, falls back to the instance's existing value for whichever of
    the two fields wasn't supplied in this request. Both sides are run
    through to_date() since self.instance.date_received can still be a raw
    datetime (see to_date()'s docstring) if the instance was created
    without an explicit date_received and never re-fetched from the DB.
    """
    request = self.context.get('request')
    if request and request.user.role != 'admin' and 'unit_price' in attrs:
      raise serializers.ValidationError({'unit_price': 'Only an admin can override prices.'})
    if (self.instance is not None and 'ingredient' in attrs and
        attrs['ingredient'].pk != self.instance.ingredient_id):
      raise serializers.ValidationError({
        'ingredient_id': 'A batch cannot be reassigned to another ingredient.'
      })

    if self.instance is not None:
      date_received = to_date(attrs.get('date_received', self.instance.date_received))
      expiration_date = to_date(attrs.get('expiration_date', self.instance.expiration_date))
    else:
      date_received = to_date(attrs.get('date_received')) or timezone.localdate()
      expiration_date = to_date(attrs.get('expiration_date'))

    if expiration_date is not None and date_received is not None and expiration_date < date_received:
      raise serializers.ValidationError({
        'expiration_date': 'Expiration date cannot be before date received.'
      })
    if date_received is not None and date_received > timezone.localdate():
      raise serializers.ValidationError({'date_received': 'Date received cannot be in the future.'})
    return attrs

  def create(self, validated_data):
    with transaction.atomic():
      quantity = validated_data.pop('quantity')
      validated_data['initial_quantity'] = quantity
      validated_data['remaining_quantity'] = quantity
      if validated_data.get('unit_price') is None:
          validated_data['unit_price'] = validated_data['ingredient'].unit_price

      business_date = timezone.localdate()
      seq = next_sequence('ING', business_date=business_date)
      validated_data['batch_number'] = generate_batch_number('ING', seq, business_date=business_date)
      return super().create(validated_data)

  def update(self, instance, validated_data):
    validated_data.pop('quantity', None)
    with transaction.atomic():
      current = IngredientBatch.objects.select_for_update().get(pk=instance.pk)
      self.instance = current
      validated_data = self.validate(validated_data)
      return super().update(current, validated_data)
