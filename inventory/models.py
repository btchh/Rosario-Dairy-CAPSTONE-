from django.utils import timezone
from django.db import models

# PRODUCT AND INGREDIENT MODELS

class Product(models.Model):
  name = models.CharField(max_length=100)
  category = models.CharField(max_length=100)
  unit = models.CharField(max_length=50)
  unit_price = models.DecimalField(max_digits=10, decimal_places=2)
  shelf_life = models.IntegerField()
  low_stock_threshold = models.IntegerField(default=10)
  is_active = models.BooleanField(default=True)
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  def __str__(self):
    return self.name

  class Meta:
    verbose_name = "Product"
    verbose_name_plural = "Products"
    ordering = ['name']

class Ingredient(models.Model):
  name = models.CharField(max_length=100)
  unit = models.CharField(max_length=50)
  unit_price = models.DecimalField(max_digits=10, decimal_places=2)
  shelf_life = models.IntegerField()
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

# PRODUCT AND INGREDIENT BATCH MODELS

# TODO: Create a separate table for IngredientBatch and ProductBatch to track batches of ingredients and products, including their expiration dates and quantities.

class IngredientBatch(models.Model):
  ingredient = models.ForeignKey('Ingredient', on_delete=models.CASCADE, related_name='batches')
  batch_number = models.CharField(max_length=50)
  initial_quantity = models.DecimalField(max_digits=10, decimal_places=2)
  remaining_quantity = models.DecimalField(max_digits=10, decimal_places=2)
  expiration_date = models.DateField()
  date_received = models.DateField(default=timezone.now)
  status = models.CharField(max_length=20, choices=[('available', 'Available'), ('depleted', 'Depleted'), ('expired', 'Expired'), ('disposed', 'Disposed')], default='available')
  notes = models.TextField(blank=True, null=True)
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  def __str__(self):
    return f"{self.ingredient.name} - Batch {self.batch_number}"
  
  class Meta:
    verbose_name = "Ingredient Batch"
    verbose_name_plural = "Ingredient Batches"
    ordering = ['expiration_date']


class ProductBatch(models.Model):
  product = models.ForeignKey('Product', on_delete=models.CASCADE, related_name='batches')
  batch_number = models.CharField(max_length=50)
  initial_quantity = models.DecimalField(max_digits=10, decimal_places=2)
  remaining_quantity = models.DecimalField(max_digits=10, decimal_places=2)
  expiration_date = models.DateField()
  date_received = models.DateField(default=timezone.now)
  status = models.CharField(max_length=20, choices=[('available', 'Available'), ('depleted', 'Depleted'), ('expired', 'Expired'), ('disposed', 'Disposed')], default='available')
  notes = models.TextField(blank=True, null=True)
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  def __str__(self):
    return f"{self.product.name} - Batch {self.batch_number}"
  
  class Meta:
    verbose_name = "Product Batch"
    verbose_name_plural = "Product Batches"
    ordering = ['expiration_date']