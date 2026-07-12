from django.db import models
from django.utils import timezone
from django.conf import settings

class StockCount(models.Model):
  ingredient_batch = models.ForeignKey('IngredientBatch', on_delete=models.PROTECT, related_name='counts', blank=True, null=True)
  product_batch = models.ForeignKey('ProductBatch', on_delete=models.PROTECT, related_name='counts', blank=True, null=True)
  expected_quantity = models.DecimalField(max_digits=10, decimal_places=2)
  counted_quantity = models.DecimalField(max_digits=10, decimal_places=2)
  variance = models.DecimalField(max_digits=10, decimal_places=2)
  counted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='stock_counts')
  count_date = models.DateField(default=timezone.now)
  notes = models.TextField(blank=True, null=True)
  resulting_adjustment = models.ForeignKey('StockAdjustment', on_delete=models.SET_NULL, related_name='+', blank=True, null=True)
  created_at = models.DateTimeField(auto_now_add=True)

  def __str__(self):
    target = self.ingredient_batch or self.product_batch
    return f"Count: {target} on {self.count_date}"

  class Meta:
    verbose_name = "Stock Count"
    verbose_name_plural = "Stock Counts"
    ordering = ['-count_date']