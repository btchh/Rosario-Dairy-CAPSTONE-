from django.conf import settings
from django.db import models


class NotificationState(models.Model):
    """A user's acknowledgement of a specific revision of a live alert."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_states')
    notification_key = models.CharField(max_length=100)
    revision = models.CharField(max_length=64)
    read_at = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'notification_key', 'revision'], name='unique_user_notification_revision')]
