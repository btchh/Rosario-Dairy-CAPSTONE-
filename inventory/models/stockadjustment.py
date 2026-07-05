from django.db import models
from django.conf import settings

class StockAdjustment(models.Model):
  ADJUSTMENT_TYPES = [
    ('expired', 'Expired'),
    ('spoilage', 'Spoilage'),
    ('spillage', 'Spillage'),
    ('taste_test', 'Taste Test Rejection'),
    ('correction', 'Correction'),
  ]

  product_batch = models.ForeignKey('ProductBatch', on_delete=models.PROTECT, related_name='adjustments', blank=True, null=True)
  ingredient_batch = models.ForeignKey('IngredientBatch', on_delete=models.PROTECT, related_name='adjustments', blank=True, null=True)
  adjustment_type = models.CharField(max_length=20, choices=ADJUSTMENT_TYPES)
  quantity = models.DecimalField(max_digits=10, decimal_places=2)
  unit_cost = models.DecimalField(max_digits=10, decimal_places=2)
  reason = models.TextField(blank=True, null=True)
  adjusted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='adjustments') 
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  def __str__(self):
    return self.adjustment_type