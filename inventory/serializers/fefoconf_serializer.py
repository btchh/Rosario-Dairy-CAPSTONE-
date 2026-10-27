from datetime import date

from django.utils import timezone
from rest_framework import serializers
from ..models import FEFOConf


class FEFOConfSerializer(serializers.ModelSerializer):
  class Meta:
    model = FEFOConf
    fields = [
      'id', 'near_expiry_threshold', 'low_stock_threshold',
      'critical_expiry_threshold', 'updated_at'
    ]
    read_only_fields = ['id', 'updated_at']

  def validate(self, attrs):
    instance = self.instance or FEFOConf()
    near_expiry = attrs.get(
      'near_expiry_threshold', instance.near_expiry_threshold
    )
    critical_expiry = attrs.get(
      'critical_expiry_threshold', instance.critical_expiry_threshold
    )
    low_stock = attrs.get('low_stock_threshold', instance.low_stock_threshold)

    errors = {}
    max_expiry_days = (date.max - timezone.localdate()).days
    if near_expiry < 0:
      errors['near_expiry_threshold'] = 'Must be zero or greater.'
    elif near_expiry > max_expiry_days:
      errors['near_expiry_threshold'] = f'Must not exceed {max_expiry_days} days (the supported calendar range).'
    if critical_expiry < 0:
      errors['critical_expiry_threshold'] = 'Must be zero or greater.'
    elif critical_expiry > max_expiry_days:
      errors['critical_expiry_threshold'] = f'Must not exceed {max_expiry_days} days (the supported calendar range).'
    if critical_expiry > near_expiry:
      errors['critical_expiry_threshold'] = (
        'Must not exceed the near-expiry threshold.'
      )
    if low_stock < 0:
      errors['low_stock_threshold'] = 'Must be zero or greater.'
    if errors:
      raise serializers.ValidationError(errors)
    return attrs
