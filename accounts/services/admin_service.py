from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction as db_transaction
from typing import cast
from accounts.models import Users


def update_user(pk, data, requesting_user):
    """
    Raises Users.DoesNotExist if the target user isn't found.
    Raises ValueError (with a user-facing message) for any validation failure.
    """
    with db_transaction.atomic():
        user = cast(Users, Users.objects.select_for_update().get(pk=pk))
        is_self = user.pk == requesting_user.pk

        if is_self and ('role' in data or 'is_active' in data):
            raise ValueError("You cannot change your own role or active status.")

        if 'role' in data and data['role'] not in [c[0] for c in Users.ROLE_CHOICES]:
            valid_roles = [c[0] for c in Users.ROLE_CHOICES]
            raise ValueError(f"Invalid role. Must be one of {valid_roles}.")

        if 'email' in data:
            try:
                validate_email(data['email'])
            except ValidationError:
                raise ValueError('Invalid email format.')

        demoting = user.role == 'admin' and data.get('role', user.role) != 'admin'
        deactivating = user.role == 'admin' and user.is_active and data.get('is_active', True) is False
        if demoting or deactivating:
            list(Users.objects.select_for_update().filter(role='admin', is_active=True))
            remaining_admins = Users.objects.filter(role='admin', is_active=True).exclude(pk=user.pk).count()
            if remaining_admins == 0:
                raise ValueError("Cannot remove the last active admin.")

        non_reactivatable = ['terminated', 'resigned']
        if data.get('is_active') is True and not user.is_active:
            if user.deactivation_reason in non_reactivatable:
                reason_label = dict(Users.DEACTIVATION_REASONS).get(
                    user.deactivation_reason, user.deactivation_reason
                )
                raise ValueError(f"This user was {reason_label} and cannot be reactivated directly.")
            user.deactivation_reason = 'none'

        for field in ['role', 'is_active', 'email', 'first_name', 'last_name', 'phone_number', 'address']:
            if field in data:
                setattr(user, field, data[field])

        try:
            user.save()
        except IntegrityError:
            raise ValueError('Username or email already exists.')

        return user


def deactivate_user(pk, requesting_user, reason='suspended'):
    valid_reasons = [c[0] for c in Users.DEACTIVATION_REASONS if c[0] != 'none']
    if reason not in valid_reasons:
        raise ValueError(f"Invalid reason. Must be one of {valid_reasons}.")

    with db_transaction.atomic():
        list(Users.objects.select_for_update().filter(role='admin', is_active=True))
        user = cast(Users, Users.objects.select_for_update().get(pk=pk))

        if user.pk == requesting_user.pk:
            raise ValueError("You cannot deactivate your own account.")

        if not user.is_active:
            raise ValueError("User is already deactivated.")

        if user.role == 'admin':
            remaining_admins = Users.objects.filter(role='admin', is_active=True).exclude(pk=user.pk).count()
            if remaining_admins == 0:
                raise ValueError("Cannot deactivate the last active admin.")

        user.is_active = False
        user.deactivation_reason = reason
        user.save()
        return user