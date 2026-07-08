from django.db import models

class Product(models.Model):
  category = models.ForeignKey('Category', on_delete=models.PROTECT, related_name='products')
  name = models.CharField(max_length=100)
  unit = models.CharField(max_length=50)
  unit_price = models.DecimalField(max_digits=10, decimal_places=2)
  variant = models.CharField(max_length=100, blank=True,null=True)
  shelf_life = models.IntegerField()
  low_stock_threshold = models.IntegerField(default=10)
  is_active = models.BooleanField(default=True)
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  def __str__(self):
    return f"{self.name} {self.variant}" if self.variant else self.name

  class Meta:
    verbose_name = "Product"
    verbose_name_plural = "Products"
    ordering = ['name']