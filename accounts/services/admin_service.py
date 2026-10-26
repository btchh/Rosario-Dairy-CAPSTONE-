from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction as db_transaction
from typing import cast
from accounts.models import Users, PasswordResetChallenge, EmailChangeChallenge


_ADMIN_EDITABLE_FIELDS = {
    'username', 'role', 'is_active', 'deactivation_reason', 'email', 'first_name',
    'last_name', 'phone_number', 'address',
}


def _validation_message(exc):
    return ' '.join(exc.messages)


def update_user(pk, data, requesting_user):
    """
    Raises Users.DoesNotExist if the target user isn't found.
    Raises ValueError (with a user-facing message) for any validation failure.
    """
    with db_transaction.atomic():
        user = cast(Users, Users.objects.select_for_update().get(pk=pk))
        data = {key: data.get(key) for key in data}
        current_password = data.pop('current_password', None)
        unknown = sorted(set(data) - _ADMIN_EDITABLE_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported field(s): {', '.join(unknown)}.")

        if 'is_active' in data and type(data['is_active']) is not bool:
            raise ValueError("'is_active' must be a boolean.")

        is_self = user.pk == requesting_user.pk
        if is_self and 'email' in data and data['email'] != user.email:
            raise ValueError('Use /accounts/email-change/ to verify your recovery email.')
        if is_self and 'username' in data and data['username'] != user.username:
            if not isinstance(current_password, str) or not user.check_password(current_password):
                raise ValueError('Current password is required to change your username.')

        if is_self and any(
            field in data for field in ('role', 'is_active', 'deactivation_reason')
        ):
            raise ValueError("You cannot change your own role or active status.")

        if 'role' in data and data['role'] not in [c[0] for c in Users.ROLE_CHOICES]:
            valid_roles = [c[0] for c in Users.ROLE_CHOICES]
            raise ValueError(f"Invalid role. Must be one of {valid_roles}.")

        if 'username' in data:
            if not isinstance(data['username'], str) or not data['username'].strip():
                raise ValueError('Username is required.')
            data['username'] = data['username'].strip()
            if Users.objects.filter(username__iexact=data['username']).exclude(pk=user.pk).exists():
                raise ValueError('Username already exists.')

        if 'email' in data:
            try:
                data['email'] = Users.objects.normalize_email(data['email'].strip())
                validate_email(data['email'])
            except (AttributeError, TypeError, ValidationError):
                raise ValueError('Invalid email format.')
            if Users.objects.filter(email__iexact=data['email']).exclude(pk=user.pk).exists():
                raise ValueError('Username or email already exists.')

        valid_reasons = [c[0] for c in Users.DEACTIVATION_REASONS if c[0] != 'none']
        if data.get('is_active') is False:
            reason = data.get('deactivation_reason', 'suspended')
            if reason not in valid_reasons:
                raise ValueError(f"Invalid reason. Must be one of {valid_reasons}.")
            data['deactivation_reason'] = reason
        elif data.get('is_active') is True:
            data['deactivation_reason'] = 'none'
        elif 'deactivation_reason' in data:
            raise ValueError("'deactivation_reason' requires an active-status change.")

        demoting = user.role == 'admin' and data.get('role', user.role) != 'admin'
        deactivating = user.role == 'admin' and user.is_active and data.get('is_active', True) is False
        if demoting or deactivating:
            list(Users.objects.select_for_update().filter(role='admin', is_active=True))
            remaining_admins = Users.objects.filter(role='admin', is_active=True).exclude(pk=user.pk).count()
            if remaining_admins == 0:
                raise ValueError("Cannot remove the last active admin.")

        if user.is_active and data.get('is_active') is False:
            user.token_version += 1
            PasswordResetChallenge.objects.filter(user=user).delete()
            EmailChangeChallenge.objects.filter(user=user).delete()

        for field in _ADMIN_EDITABLE_FIELDS:
            if field in data:
                setattr(user, field, data[field])

        if {'email', 'username'} & data.keys():
            PasswordResetChallenge.objects.filter(user=user).delete()
            EmailChangeChallenge.objects.filter(user=user).delete()

        try:
            user.full_clean(exclude=['password'])
            user.save()
        except ValidationError as exc:
            raise ValueError(_validation_message(exc)) from exc
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
        user.token_version += 1
        user.deactivation_reason = reason
        user.save()
        PasswordResetChallenge.objects.filter(user=user).delete()
        EmailChangeChallenge.objects.filter(user=user).delete()
        return user
