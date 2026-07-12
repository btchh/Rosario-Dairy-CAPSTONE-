from django.db import models
from django.conf import settings

# Create your models here.

class Customer(models.Model):
  name = models.CharField(max_length=255)
  contact_number = models.CharField(max_length=15,blank=True, null=True)
  email = models.EmailField(blank=True, null=True)
  address = models.TextField(blank=True, null=True)
  created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
  created_at = models.DateTimeField(auto_now_add=True)
  updated_at = models.DateTimeField(auto_now=True)

  def __str__(self):
    return self.name