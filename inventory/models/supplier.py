from django.db import models

class Supplier(models.Model):
  name = models.CharField(max_length=255)
  contact_number = models.CharField(max_length=15, blank=True, null=True)
  address = models.TextField(blank=True, null=True)
  is_active = models.BooleanField(default=True)
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  def __str__(self):
    return self.name
