from django.db import models

class TransactionItem(models.Model):
  transaction = models.ForeignKey('Transaction', on_delete=models.PROTECT, related_name='items')
  product_batch = models.ForeignKey('inventory.ProductBatch', on_delete=models.PROTECT, related_name='+')
  quantity = models.DecimalField(max_digits=10, decimal_places=2)
  unit_price = models.DecimalField(max_digits=10, decimal_places=2)  # snapshot

  def __str__(self):
    return f"{self.quantity} x {self.product_batch.batch_number}"