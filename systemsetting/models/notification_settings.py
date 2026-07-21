from django.db import models

class NotificationSettings(models.Model):
  low_stock_alerts = models.BooleanField(default=True)
  near_expiry_alerts = models.BooleanField(default=True)
  new_order_alerts = models.BooleanField(default=True)
  forecast_warnings = models.BooleanField(default=True)
  report_ready_notifications = models.BooleanField(default=False)

  updated_at = models.DateTimeField(auto_now=True)

  def save(self, *args, **kwargs):
    self.pk = 1
    super().save(*args, **kwargs)

  @classmethod
  def get_config(cls):
    config, _ = cls.objects.get_or_create(pk=1)
    return config

  def __str__(self):
    return "Notification Settings"

  class Meta:
    verbose_name = "Notification Settings"