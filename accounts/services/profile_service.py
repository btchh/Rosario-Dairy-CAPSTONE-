from typing import cast
from datetime import timedelta
from django.utils import timezone
from accounts.models import Users
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import IntegrityError

_SELF_EDIT_BLOCKED_FIELDS = ('role', 'is_active', 'is_superuser', 'is_staff', 'username', 'deactivation_reason')

_SELF_EDIT_ALLOWED_FIELDS = ('first_name', 'last_name', 'phone_number', 'address', 'email')

PROFILE_EDIT_COOLDOWN_MINUTES = 5

def get_user(user):
    return user

def get_user_detail(pk):
    return cast(Users, Users.objects.get(pk=pk))

def update_own_profile(user, data):
    """
    Self-service profile update for the currently authenticated user
    (admin or staff — both call this the same way). Only first_name,
    last_name, phone_number, address, and email may be changed here.
    """
    if user.last_profile_update_at is not None:
        elapsed = timezone.now() - user.last_profile_update_at
        cooldown = timedelta(minutes=PROFILE_EDIT_COOLDOWN_MINUTES)
        if elapsed < cooldown:
            remaining = int((cooldown - elapsed).total_seconds())
            raise ValueError(
                f"Profile can only be updated once every {PROFILE_EDIT_COOLDOWN_MINUTES} "
                f"minutes. Try again in {remaining // 60 + 1} minute(s)."
            )

    blocked = [f for f in _SELF_EDIT_BLOCKED_FIELDS if f in data]
    if blocked:
        raise ValueError(
            f"Cannot change the following field(s) via profile update: {', '.join(blocked)}."
        )

    if 'email' in data:
        try:
            validate_email(data['email'])
        except ValidationError:
            raise ValueError("Invalid email format.")

    for field in _SELF_EDIT_ALLOWED_FIELDS:
        if field in data:
            setattr(user, field, data[field])

    user.last_profile_update_at = timezone.now()
    try:
        user.save()
    except IntegrityError:
        raise ValueError("Email already in use.")

    return user