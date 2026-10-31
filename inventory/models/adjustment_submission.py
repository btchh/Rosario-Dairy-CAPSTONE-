from django.conf import settings
from django.db import models


class StockAdjustmentSubmission(models.Model):
    """Durable identity and response for a keyed inventory adjustment."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    request_id = models.UUIDField()
    fingerprint = models.CharField(max_length=64)
    adjustment = models.OneToOneField('StockAdjustment', on_delete=models.PROTECT, related_name='+')
    response_data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['user', 'request_id'], name='stock_adjustment_user_request_unique')]
