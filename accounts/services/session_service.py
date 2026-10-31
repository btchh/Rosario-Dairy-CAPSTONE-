"""Server-owned idle deadlines; ordinary requests never count as user activity."""
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import LoginSession


def session_error(code, message):
    return AuthenticationFailed({'detail': message, 'code': code}, code=code)


def create_session_token(user):
    now = timezone.now()
    session = LoginSession.objects.create(
        user=user, last_activity_at=now,
        idle_expires_at=now + timedelta(seconds=settings.AUTH_IDLE_TIMEOUT_SECONDS),
    )
    token = RefreshToken.for_user(user)
    token['token_version'] = user.token_version
    token['sid'] = str(session.pk)
    return token


def check_session(user, token, *, for_update=False):
    try:
        session_id = UUID(str(token.get('sid', '')))
    except (ValueError, TypeError, AttributeError):
        raise session_error('session_required', 'Please sign in again.') from None
    sessions = LoginSession.objects
    if for_update:
        sessions = sessions.select_for_update()
    session = sessions.filter(pk=session_id, user=user).first()
    if session is None:
        raise session_error('session_required', 'Please sign in again.')
    # Check after acquiring the row lock: waiting must never revive a deadline.
    if session.idle_expires_at <= timezone.now():
        raise session_error(
            'session_idle_timeout', 'You were signed out due to inactivity. Please sign in again.'
        )
    return session


def session_payload(session):
    return {
        'id': str(session.pk),
        'idle_timeout_seconds': settings.AUTH_IDLE_TIMEOUT_SECONDS,
        'warning_seconds': settings.AUTH_IDLE_WARNING_SECONDS,
        'server_time': timezone.now().isoformat(),
        'last_activity_at': session.last_activity_at.isoformat(),
        'idle_expires_at': session.idle_expires_at.isoformat(),
    }


@transaction.atomic
def record_activity(user, token):
    session = check_session(user, token, for_update=True)
    now = timezone.now()
    # Recheck at the write instant, including the exact expiry boundary.
    if session.idle_expires_at <= now:
        raise session_error('session_idle_timeout', 'You were signed out due to inactivity. Please sign in again.')
    session.last_activity_at = now
    session.idle_expires_at = now + timedelta(seconds=settings.AUTH_IDLE_TIMEOUT_SECONDS)
    session.save(update_fields=['last_activity_at', 'idle_expires_at'])
    return session
