from django.db import models
from django.conf import settings
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.db.models import Manager
    from .order_item import OrderItem

class Order(models.Model):
  STATUS_CHOICES = [
    ('fulfilled', 'Fulfilled'),
    ('cancelled', 'Cancelled'),
  ]
  DISCOUNT_CHOICES = [
    ('none', 'None'),
    ('percent', 'Percentage'),
    ('fixed', 'Fixed Amount'),
  ]

  customer = models.ForeignKey('Customer', on_delete=models.PROTECT, related_name='orders')
  handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='orders_handled')
  status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='fulfilled')
  discount_type = models.CharField(max_length=10, choices=DISCOUNT_CHOICES, default='none')
  discount_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
  transaction = models.ForeignKey('Transaction', on_delete=models.SET_NULL, blank=True, null=True, related_name='+')
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  if TYPE_CHECKING:
    items: "Manager[OrderItem]"

  def __str__(self):
    return f"Order #{self.pk} - {self.customer.name} ({self.status})"