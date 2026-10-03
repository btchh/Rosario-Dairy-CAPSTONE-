from django.db import models

class TransactionItem(models.Model):
  transaction = models.ForeignKey('Transaction', on_delete=models.PROTECT, related_name='items')
  product_batch = models.ForeignKey('inventory.ProductBatch', on_delete=models.PROTECT, related_name='+')
  quantity = models.DecimalField(max_digits=10, decimal_places=2)
  unit_price = models.DecimalField(max_digits=10, decimal_places=2)  # snapshot
  source_product_label = models.CharField(max_length=100, blank=True)
  source_line_total = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
  product_id_snapshot = models.PositiveBigIntegerField(null=True, editable=False)
  product_name_snapshot = models.CharField(max_length=100, null=True, editable=False)
  product_variant_snapshot = models.CharField(max_length=100, blank=True, null=True, editable=False)
  category_id_snapshot = models.PositiveBigIntegerField(null=True, editable=False)
  category_name_snapshot = models.CharField(max_length=100, null=True, editable=False)

  def __str__(self):
    return f"{self.quantity} x {self.product_batch.batch_number}"
