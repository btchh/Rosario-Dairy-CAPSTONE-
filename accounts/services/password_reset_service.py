import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from accounts.models import PasswordResetChallenge
from systemsetting.runtime import get_brand_name
from . import auth_service


logger = logging.getLogger(__name__)
User = get_user_model()


def _identity(username, email):
    return f'{username}\0{email.casefold()}'


def _otp_digest(identity, otp):
    return salted_hmac(
        'accounts.password-reset-otp', f'{identity}:{otp}'
    ).hexdigest()


def _matching_active_user(username, email):
    user = User.objects.filter(username=username, is_active=True).first()
    if user is None or (user.email or '').casefold() != email.casefold():
        return None
    return user


def request_password_reset(username, email):
    """Send an OTP when credentials match, without revealing match status."""
    user = _matching_active_user(username, email)
    if user is None:
        return

    now = timezone.now()
    cooldown = timedelta(seconds=settings.PASSWORD_RESET_OTP_RESEND_COOLDOWN)
    identity = _identity(user.username, user.email)
    otp = f'{secrets.randbelow(1_000_000):06d}'
    otp_digest = _otp_digest(identity, otp)

    with transaction.atomic():
        # Locking the user serializes first-time requests even before a
        # PasswordResetChallenge row exists.
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        challenge = PasswordResetChallenge.objects.filter(user=locked_user).first()
        if challenge and challenge.last_sent_at + cooldown > now:
            return
        PasswordResetChallenge.objects.update_or_create(
            user=locked_user,
            defaults={
                'otp_digest': otp_digest,
                'expires_at': now + timedelta(seconds=settings.PASSWORD_RESET_OTP_TIMEOUT),
                'failed_attempts': 0,
                'last_sent_at': now,
            },
        )

    minutes = max(1, settings.PASSWORD_RESET_OTP_TIMEOUT // 60)
    brand_name = get_brand_name()
    try:
        send_mail(
            subject=f'{brand_name} password reset code',
            message=(
                f'Your {brand_name} password reset code is: {otp}\n\n'
                f'This code expires in {minutes} minutes. If you did not '
                'request it, you can ignore this email.'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception:
        # Only remove the challenge created by this request. A newer request
        # must not be invalidated if mail delivery completes out of order.
        PasswordResetChallenge.objects.filter(
            user=user, otp_digest=otp_digest
        ).delete()
        logger.exception('Unable to send password reset email')


def reset_password_with_otp(username, email, otp, new_password):
    """Validate and consume an OTP, returning False for invalid credentials."""
    user = _matching_active_user(username, email)
    if user is None:
        return False

    identity = _identity(user.username, user.email)
    with transaction.atomic():
        challenge = (
            PasswordResetChallenge.objects.select_for_update()
            .filter(user=user)
            .first()
        )
        if challenge is None:
            return False

        if challenge.expires_at <= timezone.now():
            challenge.delete()
            return False

        if not constant_time_compare(
            challenge.otp_digest, _otp_digest(identity, otp)
        ):
            challenge.failed_attempts += 1
            if challenge.failed_attempts >= settings.PASSWORD_RESET_OTP_MAX_ATTEMPTS:
                challenge.delete()
            else:
                challenge.save(update_fields=['failed_attempts'])
            return False

        auth_service.forgot_password(user, new_password)
        challenge.delete()
        return True
