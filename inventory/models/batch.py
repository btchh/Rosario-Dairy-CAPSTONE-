from decimal import Decimal
from django.core.validators import MinValueValidator
from django.utils import timezone
from django.db import models

GRADE_CHOICES = [
  ('A', 'Class A'),
  ('B', 'Class B')
]

class IngredientBatch(models.Model):
  STATUS_TYPES = [
    ('available', 'Available'),
    ('depleted', 'Depleted'), 
    ('expired', 'Expired'),
    ('disposed', 'Disposed')]

  ingredient = models.ForeignKey('Ingredient', on_delete=models.PROTECT, related_name='batches')
  batch_number = models.CharField(max_length=50, unique=True)
  supplier = models.ForeignKey('Supplier', on_delete=models.PROTECT, related_name='batches', blank=True, null=True)
  grade = models.CharField(max_length=20,choices=GRADE_CHOICES, blank=True, null=True)
  unit_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, validators=[MinValueValidator(Decimal('0.00'))])
  initial_quantity = models.DecimalField(max_digits=10, decimal_places=2)
  remaining_quantity = models.DecimalField(max_digits=10, decimal_places=2)
  expiration_date = models.DateField()
  date_received = models.DateField(default=timezone.now)
  status = models.CharField(max_length=20, choices=STATUS_TYPES, default='available')
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
  STATUS_TYPES = [
    ('available', 'Available'), 
    ('depleted', 'Depleted'), 
    ('expired', 'Expired'), 
    ('disposed', 'Disposed')
  ]

  product = models.ForeignKey('Product', on_delete=models.PROTECT, related_name='batches')
  batch_number = models.CharField(max_length=50, unique=True)
  grade = models.CharField(max_length=20,choices=GRADE_CHOICES, blank=True, null=True)
  unit_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, validators=[MinValueValidator(Decimal('0.00'))])
  initial_quantity = models.DecimalField(max_digits=10, decimal_places=2)
  remaining_quantity = models.DecimalField(max_digits=10, decimal_places=2)
  expiration_date = models.DateField()
  date_received = models.DateField(default=timezone.now)
  status = models.CharField(max_length=20, choices=STATUS_TYPES, default='available')
  notes = models.TextField(blank=True, null=True)
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  def __str__(self):
    return f"{self.product.name} - Batch {self.batch_number}"
  
  class Meta:
    verbose_name = "Product Batch"
    verbose_name_plural = "Product Batches"
    ordering = ['expiration_date']

# General Rule:
# When logging a batch, staff must identify the item with the nearest expiration date
# among all units in the delivery and use that date as the batch expiration_date.
# This ensures the system alerts based on the most time-sensitive item in the batch.