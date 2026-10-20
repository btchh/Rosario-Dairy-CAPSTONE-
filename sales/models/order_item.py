from django.db import models

class OrderItem(models.Model):
  order = models.ForeignKey('Order', on_delete=models.CASCADE, related_name='items')
  product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='+')
  quantity = models.DecimalField(max_digits=10, decimal_places=2)
  unit_price = models.DecimalField(max_digits=10, decimal_places=2)  # snapshot at order time
  subtotal = models.DecimalField(max_digits=20, decimal_places=2)
  discount_type = models.CharField(max_length=10, choices=[('none', 'None'), ('percent', 'Percentage'), ('fixed', 'Fixed Amount')], default='none')
  discount_value = models.DecimalField(max_digits=20, decimal_places=2, default=0)
  discount_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
  total_amount = models.DecimalField(max_digits=20, decimal_places=2, null=True)

  def __str__(self):
    return f"{self.quantity} x {self.product.name} (Order #{self.order.pk})"
