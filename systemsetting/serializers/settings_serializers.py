from rest_framework import serializers
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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

  def validate_currency(self, value):
    value = value.strip().upper()
    if not re.fullmatch(r'[A-Z]{3}', value):
      raise serializers.ValidationError('Use a three-letter currency code such as PHP or USD.')
    return value

  def validate_date_format(self, value):
    supported = {'MM/DD/YYYY', 'DD/MM/YYYY', 'YYYY-MM-DD'}
    if value not in supported:
      raise serializers.ValidationError(f'Must be one of: {", ".join(sorted(supported))}.')
    return value

  def validate_timezone(self, value):
    try:
      ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
      raise serializers.ValidationError('Enter a valid IANA timezone, such as Asia/Manila.') from exc
    return value

  def validate_language(self, value):
    if not re.fullmatch(r'[a-z]{2}(?:-[A-Z]{2})?', value):
      raise serializers.ValidationError('Use a language code such as en or en-PH.')
    return value

  def validate_business_email(self, value):
    return value.strip().casefold()
