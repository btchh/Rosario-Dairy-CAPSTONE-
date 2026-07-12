from django.db import models

class Ingredient(models.Model):
  INGREDIENT_TYPES = [
    ('raw_milk', 'Raw Milk'),
    ('processing', 'Processing'),
    ('packaging', 'Packaging')
  ]

  name = models.CharField(max_length=100)
  unit = models.CharField(max_length=50)
  unit_price = models.DecimalField(max_digits=10, decimal_places=2)
  shelf_life = models.IntegerField()
  ingredient_type = models.CharField(max_length=20, choices=INGREDIENT_TYPES, default='raw_milk')
  low_stock_threshold = models.IntegerField(default=10)
  is_active = models.BooleanField(default=True)
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  def __str__(self):
    return self.name

  class Meta:
    verbose_name = "Ingredient"
    verbose_name_plural = "Ingredients"
    ordering = ['name']