from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken
from accounts.authentication import check_token_version
from accounts.models import PasswordResetChallenge, EmailChangeChallenge

PASSWORD_CHANGE_COOLDOWN_MINUTES = 15


def change_password(user, old_password, new_password):
    if not isinstance(old_password, str) or not isinstance(new_password, str):
        raise ValueError('Passwords must be text.')
    with transaction.atomic():
        user.refresh_from_db(from_queryset=type(user).objects.select_for_update())
        if not user.is_active:
            raise ValueError('This account is inactive.')
        if user.last_password_change_at is not None:
            elapsed = timezone.now() - user.last_password_change_at
            cooldown = timedelta(minutes=PASSWORD_CHANGE_COOLDOWN_MINUTES)
            if elapsed < cooldown:
                remaining = int((cooldown - elapsed).total_seconds())
                raise ValueError(
                    f"Password can only be changed once every {PASSWORD_CHANGE_COOLDOWN_MINUTES} "
                    f"minutes. Try again in {remaining // 60 + 1} minute(s)."
                )

        if not user.check_password(old_password):
            raise ValueError("Old Password is Incorrect")
        try:
            validate_password(new_password, user)
        except ValidationError as e:
            raise ValueError(" ".join(str(m) for m in e.messages))
        user.set_password(new_password)
        user.token_version += 1
        user.last_password_change_at = timezone.now()
        user.save(update_fields=['password', 'token_version', 'last_password_change_at'])
        PasswordResetChallenge.objects.filter(user=user).delete()
        EmailChangeChallenge.objects.filter(user=user).delete()


def forgot_password(user, new_password):
    """
    Admin-initiated reset (AdminResetPasswordView). Deliberately bypasses the
    self-service cooldown (doesn't touch last_password_change_at) — see
    AdminResetPasswordCooldownExemptionTests. Also clears any active login
    lockout: an admin resetting a locked-out user's password should let them
    log back in immediately with the new password, not leave them 429'd
    until the 15-minute lockout window expires on its own.
    """
    if not isinstance(new_password, str):
        raise ValueError('Password must be text.')
    with transaction.atomic():
        user.refresh_from_db(from_queryset=type(user).objects.select_for_update())
        try:
            validate_password(new_password, user)
        except ValidationError as e:
            raise ValueError(" ".join(str(m) for m in e.messages))
        user.set_password(new_password)
        user.token_version += 1
        user.failed_login_attempts = 0
        user.locked_until = None
        user.save(update_fields=['password', 'token_version', 'failed_login_attempts', 'locked_until'])
        PasswordResetChallenge.objects.filter(user=user).delete()
        EmailChangeChallenge.objects.filter(user=user).delete()


@transaction.atomic
def logout(refresh_token, user):
    user = type(user).objects.select_for_update().get(pk=user.pk)
    token = RefreshToken(refresh_token)
    expected_user_id = getattr(user, api_settings.USER_ID_FIELD)
    if str(token.get(api_settings.USER_ID_CLAIM)) != str(expected_user_id):
        raise ValueError('Refresh token does not belong to the authenticated user.')
    check_token_version(user, token)
    token.blacklist()
    # Logout revokes all this account's access and refresh tokens immediately.
    user.token_version += 1
    user.save(update_fields=['token_version'])
