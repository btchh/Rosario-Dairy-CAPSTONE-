from django.db import models

class SystemSettings(models.Model):
  # General
  system_name = models.CharField(max_length=255, default="")
  currency = models.CharField(max_length=10, default="PHP")
  date_format = models.CharField(max_length=20, default="MM/DD/YYYY")
  timezone = models.CharField(max_length=50, default="Asia/Manila")
  language = models.CharField(max_length=50, default="en-PH")
  tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=12)

  # Business Info
  business_name = models.CharField(max_length=255, blank=True)
  business_address = models.TextField(blank=True)
  business_contact = models.CharField(max_length=15, blank=True)
  business_email = models.EmailField(blank=True)
  tin = models.CharField(max_length=50, blank=True)
  business_type = models.CharField(max_length=100, blank=True)

  updated_at = models.DateTimeField(auto_now=True)

  def save(self, *args, **kwargs):
    self.pk = 1
    super().save(*args, **kwargs)

  @classmethod
  def get_config(cls):
    config, _ = cls.objects.get_or_create(pk=1)
    return config

  def __str__(self):
    return "System Settings"

  class Meta:
    verbose_name = "System Settings"