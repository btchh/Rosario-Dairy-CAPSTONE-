from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework_simplejwt.tokens import RefreshToken

PASSWORD_CHANGE_COOLDOWN_MINUTES = 15


def change_password(user, old_password, new_password):
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
    user.last_password_change_at = timezone.now()
    user.save()


def forgot_password(user, new_password):
    """
    Admin-initiated reset (AdminResetPasswordView). Deliberately bypasses the
    self-service cooldown (doesn't touch last_password_change_at) — see
    AdminResetPasswordCooldownExemptionTests. Also clears any active login
    lockout: an admin resetting a locked-out user's password should let them
    log back in immediately with the new password, not leave them 429'd
    until the 15-minute lockout window expires on its own.
    """
    try:
        validate_password(new_password, user)
    except ValidationError as e:
        raise ValueError(" ".join(str(m) for m in e.messages))
    user.set_password(new_password)
    user.failed_login_attempts = 0
    user.locked_until = None
    user.save()


def logout(refresh_token):
    token = RefreshToken(refresh_token)
    token.blacklist()