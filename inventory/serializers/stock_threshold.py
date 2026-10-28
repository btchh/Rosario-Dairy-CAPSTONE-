from collections.abc import Mapping

from rest_framework import serializers

from ..models import FEFOConf


class StockThresholdMixin:
  """Expose the global threshold for existing readers, reject item overrides."""

  def get_low_stock_threshold(self, instance):
    # Nested and list serializers share context: read the global value once.
    if '_global_low_stock_threshold' not in self.context:
      self.context['_global_low_stock_threshold'] = FEFOConf.get_config().low_stock_threshold
    return self.context['_global_low_stock_threshold']

  def to_internal_value(self, data):
    if isinstance(data, Mapping) and 'low_stock_threshold' in data:
      raise serializers.ValidationError({
        'low_stock_threshold': 'Configure the global threshold at /settings/inventory/.',
      })
    return super().to_internal_value(data)
