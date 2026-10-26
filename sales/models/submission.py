from django.conf import settings
from django.db import models


class SaleSubmission(models.Model):
    """Durable replay identity for optional checkout/order request IDs."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    request_id = models.UUIDField()
    fingerprint = models.CharField(max_length=64)
    endpoint = models.CharField(max_length=20)
    transaction = models.OneToOneField('Transaction', on_delete=models.PROTECT, related_name='+')
    order = models.OneToOneField('Order', on_delete=models.PROTECT, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'request_id'], name='sale_submission_user_request_unique')]
