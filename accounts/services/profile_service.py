from typing import cast
from datetime import timedelta
from django.utils import timezone
from accounts.models import Users
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

_SELF_EDIT_BLOCKED_FIELDS = ('role', 'is_active', 'is_superuser', 'is_staff', 'deactivation_reason')

_SELF_EDIT_ALLOWED_FIELDS = ('username', 'first_name', 'last_name', 'phone_number', 'address', 'email')

PROFILE_EDIT_COOLDOWN_MINUTES = 5

def get_user(user):
    return user

def get_user_detail(pk):
    return cast(Users, Users.objects.get(pk=pk))

def update_own_profile(user, data):
    """
    Self-service profile update for the currently authenticated user
    (admin or staff — both call this the same way). Username, name,
    phone number, address, and email may be changed here.
    """
    data = {key: data.get(key) for key in data}
    blocked = [f for f in _SELF_EDIT_BLOCKED_FIELDS if f in data]
    if blocked:
        raise ValueError(
            f"Cannot change the following field(s) via profile update: {', '.join(blocked)}."
        )

    unknown = sorted(set(data) - set(_SELF_EDIT_ALLOWED_FIELDS))
    if unknown:
        raise ValueError(f"Unsupported field(s): {', '.join(unknown)}.")
    if not data:
        raise ValueError('At least one profile field is required.')

    if 'username' in data:
        if not isinstance(data['username'], str) or not data['username'].strip():
            raise ValueError('Username is required.')
        data['username'] = data['username'].strip()

    if 'email' in data:
        try:
            data['email'] = Users.objects.normalize_email(data['email'].strip())
            validate_email(data['email'])
        except (AttributeError, TypeError, ValidationError):
            raise ValueError("Invalid email format.")

    try:
        with transaction.atomic():
            locked_user = cast(
                Users, Users.objects.select_for_update().get(pk=user.pk)
            )
            if locked_user.last_profile_update_at is not None:
                elapsed = timezone.now() - locked_user.last_profile_update_at
                cooldown = timedelta(minutes=PROFILE_EDIT_COOLDOWN_MINUTES)
                if elapsed < cooldown:
                    remaining = int((cooldown - elapsed).total_seconds())
                    raise ValueError(
                        f"Profile can only be updated once every "
                        f"{PROFILE_EDIT_COOLDOWN_MINUTES} minutes. Try again in "
                        f"{remaining // 60 + 1} minute(s)."
                    )

            if 'email' in data and Users.objects.filter(
                email__iexact=data['email']
            ).exclude(pk=locked_user.pk).exists():
                raise ValueError("Email already in use.")

            if 'username' in data and Users.objects.filter(
                username__iexact=data['username']
            ).exclude(pk=locked_user.pk).exists():
                raise ValueError('Username already exists.')

            changed = False
            for field in _SELF_EDIT_ALLOWED_FIELDS:
                if field in data and getattr(locked_user, field) != data[field]:
                    setattr(locked_user, field, data[field])
                    changed = True
            if not changed:
                raise ValueError('No profile changes were provided.')

            locked_user.last_profile_update_at = timezone.now()
            try:
                locked_user.full_clean(exclude=['password'])
            except ValidationError as exc:
                raise ValueError(' '.join(exc.messages)) from exc
            locked_user.save()
            return locked_user
    except IntegrityError as exc:
        raise ValueError('Username or email already exists.') from exc
