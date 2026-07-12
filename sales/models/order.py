from django.db import models
from django.conf import settings

class Order(models.Model):
  STATUS_CHOICES = [
    ('placed', 'Placed'),
    ('confirmed', 'Confirmed'),
    ('fulfilled', 'Fulfilled'),
    ('cancelled', 'Cancelled')
  ]

  customer = models.ForeignKey('Customer', on_delete=models.PROTECT, related_name='orders')
  handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='orders_handled')
  status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='placed')
  transaction = models.ForeignKey('Transaction', on_delete=models.SET_NULL, blank=True, null=True, related_name='+')
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  def __str__(self):
    return f"Order #{self.pk} - {self.customer.name} ({self.status})"