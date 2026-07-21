from rest_framework import serializers
from ..models import NotificationSettings


class NotificationSettingsSerializer(serializers.ModelSerializer):
  class Meta:
    model = NotificationSettings
    fields = [
      'id', 'low_stock_alerts', 'near_expiry_alerts', 'new_order_alerts',
      'forecast_warnings', 'report_ready_notifications',
      'updated_at'
    ]
    read_only_fields = ['id', 'updated_at']