import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from accounts.models import Users, EmailChangeChallenge, PasswordResetChallenge

logger = logging.getLogger(__name__)


def _digest(user, email, otp):
    return salted_hmac('accounts.email-change', f'{user.pk}:{user.password}:{email.casefold()}:{otp}').hexdigest()


def _credentials(user, password, email):
    if not user.is_active or not isinstance(password, str) or not user.check_password(password):
        raise ValueError('Current password is required.')
    if not isinstance(email, str):
        raise ValueError('A valid email is required.')
    email = Users.objects.normalize_email(email.strip())
    try:
        validate_email(email)
    except ValidationError as exc:
        raise ValueError('A valid email is required.') from exc
    if email.casefold() == user.email.casefold():
        raise ValueError('Choose a different email address.')
    if Users.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
        raise ValueError('Email is unavailable.')
    return email


def request_email_change(user, password, email):
    with transaction.atomic():
        user = Users.objects.select_for_update().get(pk=user.pk)
        email = _credentials(user, password, email)
        now = timezone.now()
        challenge = EmailChangeChallenge.objects.filter(user=user).first()
        if challenge and challenge.last_sent_at + timedelta(seconds=60) > now:
            raise ValueError('Wait one minute before requesting another code.')
        otp = f'{secrets.randbelow(1_000_000):06d}'
        digest = _digest(user, email, otp)
        EmailChangeChallenge.objects.update_or_create(user=user, defaults={
            'email': email, 'otp_digest': digest, 'expires_at': now + timedelta(minutes=10),
            'last_sent_at': now, 'failed_attempts': 0,
        })
    try:
        send_mail('Verify your recovery email',
                  f'Your email change code is: {otp}\nThis code expires in 10 minutes.',
                  settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
    except Exception as exc:
        EmailChangeChallenge.objects.filter(user=user, otp_digest=digest).delete()
        logger.exception('Unable to send email verification')
        raise ValueError('Unable to send verification email. Try again later.') from exc


def confirm_email_change(user, password, email, otp):
    # Invalid attempts must commit; raising inside atomic would undo the counter.
    with transaction.atomic():
        user = Users.objects.select_for_update().get(pk=user.pk)
        email = _credentials(user, password, email)
        challenge = EmailChangeChallenge.objects.select_for_update().filter(user=user).first()
        if challenge is None:
            return False
        if challenge.expires_at <= timezone.now():
            challenge.delete()
            return False
        if (not isinstance(otp, str) or challenge.email.casefold() != email.casefold()
                or not constant_time_compare(challenge.otp_digest, _digest(user, email, otp))):
            challenge.failed_attempts += 1
            if challenge.failed_attempts >= 5:
                challenge.delete()
            else:
                challenge.save(update_fields=['failed_attempts'])
            return False
        old_email = user.email
        user.email = email
        user.token_version += 1
        user.last_profile_update_at = timezone.now()
        try:
            with transaction.atomic():
                user.save(update_fields=['email', 'token_version', 'last_profile_update_at'])
        except IntegrityError as exc:
            raise ValueError('Email is unavailable.') from exc
        PasswordResetChallenge.objects.filter(user=user).delete()
        challenge.delete()

        def notify():
            try:
                send_mail('Your recovery email changed',
                          'Your recovery email was changed. Contact your administrator if you did not authorize this.',
                          settings.DEFAULT_FROM_EMAIL, [old_email], fail_silently=False)
            except Exception:
                logger.exception('Unable to notify original email of recovery-email change')
        transaction.on_commit(notify)
    return True
