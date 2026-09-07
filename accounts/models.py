from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings

# Create your models here.

class Users(AbstractUser):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('staff', 'Staff')
    ]
    DEACTIVATION_REASONS = [
        ('none', 'N/A'),
        ('leave', 'On Leave'),
        ('suspended', 'Suspended'),
        ('resigned', 'Resigned'),
        ('terminated', 'Terminated'),
    ]
    
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='staff')
    deactivation_reason = models.CharField(max_length=20, choices=DEACTIVATION_REASONS, default='none')
    failed_login_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(blank=True, null=True)
    last_profile_update_at = models.DateTimeField(blank=True, null=True)
    last_password_change_at = models.DateTimeField(blank=True, null=True)


    def __str__(self):
        return self.username


class PasswordResetChallenge(models.Model):
    """A short-lived, server-side password-reset challenge for one user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='password_reset_challenge',
    )
    otp_digest = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    last_sent_at = models.DateTimeField()

    def __str__(self):
        return f'Password reset challenge for {self.user}'
