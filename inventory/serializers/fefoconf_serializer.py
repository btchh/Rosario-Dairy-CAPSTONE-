from rest_framework import serializers
from ..models import FEFOConf


class FEFOConfSerializer(serializers.ModelSerializer):
  class Meta:
    model = FEFOConf
    fields = [
      'id', 'near_expiry_threshold', 'low_stock_threshold', 'updated_at'
    ]
    read_only_fields = ['id', 'updated_at']