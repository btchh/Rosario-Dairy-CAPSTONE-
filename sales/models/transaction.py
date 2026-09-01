from django.db import models
from django.conf import settings
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.db.models import Manager
    from .transaction_item import TransactionItem

class Transaction(models.Model):
  PAYMENT_CHOICES = [
    ('cash', 'Cash'),
    ('online', 'Online'),
  ]
  DISCOUNT_CHOICES = [
    ('none', 'None'),
    ('percent', 'Percentage'),
    ('fixed', 'Fixed Amount'),
  ]

  handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='transactions_handled')
  customer = models.ForeignKey(
    'Customer', on_delete=models.PROTECT, related_name='transactions',
    null=True, blank=True
  )
  subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
  discount_type = models.CharField(max_length=10, choices=DISCOUNT_CHOICES, default='none')
  discount_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
  discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
  total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
  amount_tendered = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
  change_due = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
  payment_method = models.CharField(max_length=20, choices=PAYMENT_CHOICES, default='cash')
  delivery_status = models.CharField(max_length=50, blank=True, null=True)
  is_voided = models.BooleanField(default=False)
  created_at = models.DateTimeField(auto_now_add=True)

  if TYPE_CHECKING:
    items: "Manager[TransactionItem]"

  def __str__(self):
    return f"Transaction #{self.pk} - {self.total_amount}"
