from django.db import models

class FEFOConf(models.Model):
  near_expiry_threshold = models.IntegerField(default=7)
  global_low_stock_threshold = models.IntegerField(default=15)
  updated_at = models.DateTimeField(auto_now=True)

  def save(self, *args, **kwargs):
    self.pk = 1
    super().save(*args, **kwargs)

  @classmethod
  def get_config(cls):
    config, _ = cls.objects.get_or_create(pk=1)
    return config

  def __str__(self):
    return "Fefo Configuration"
  
  class Meta:
    verbose_name = "FEFO Configuration"