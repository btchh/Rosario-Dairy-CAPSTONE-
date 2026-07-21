from rest_framework import serializers
from ..models import SystemSettings


class SystemSettingsSerializer(serializers.ModelSerializer):
  class Meta:
    model = SystemSettings
    fields = [
      'id', 'system_name', 'currency', 'date_format', 'timezone', 'language',
      'business_name', 'business_address', 'business_contact', 'business_email', 'tin', 'business_type',
      'updated_at'
    ]
    read_only_fields = ['id', 'updated_at']