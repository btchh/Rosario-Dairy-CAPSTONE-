from django.db import models
from django.contrib.auth.models import AbstractUser

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
    profile_photo = models.ImageField(upload_to='profile_photos/', blank=True, null=True)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='staff')
    deactivation_reason = models.CharField(max_length=20, choices=DEACTIVATION_REASONS, default='none')
    failed_login_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(blank=True, null=True)
    last_profile_update_at = models.DateTimeField(blank=True, null=True)
    last_password_change_at = models.DateTimeField(blank=True, null=True)


    def __str__(self):
        return self.username