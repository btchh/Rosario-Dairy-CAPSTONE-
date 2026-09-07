# --- accounts/views/login_views.py ---
from datetime import timedelta
from typing import cast
from django.utils import timezone
from django.db import transaction
from accounts.models import Users
from accounts.throttles import LoginRateThrottle
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

LOGIN_MAX_FAILED_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15
GENERIC_LOGIN_ERROR = 'No active account found with the given credentials'


def _record_failed_login(user_pk):
    """Increment an account's failure count without losing concurrent writes."""
    with transaction.atomic():
        user = Users.objects.select_for_update().get(pk=user_pk)
        now = timezone.now()
        if user.locked_until and user.locked_until > now:
            return
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= LOGIN_MAX_FAILED_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
        user.save(update_fields=['failed_login_attempts', 'locked_until'])


class CooldownTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Wraps the default JWT login serializer with a failed-attempt lockout:
    after LOGIN_MAX_FAILED_ATTEMPTS consecutive failures, the account is
    locked for LOGIN_LOCKOUT_MINUTES. Any successful login resets the
    counter. Locked, invalid, and nonexistent accounts intentionally return
    the same generic 401 response.
    """

    def validate(self, attrs):
        username = attrs.get(self.username_field)
        user = cast(
            "Users | None",
            Users.objects.filter(**{self.username_field: username}).first(),
        )

        if user is not None and user.locked_until and user.locked_until > timezone.now():
            raise AuthenticationFailed(GENERIC_LOGIN_ERROR)

        try:
            data = super().validate(attrs)
        except AuthenticationFailed:
            if user is not None:
                _record_failed_login(user.pk)
            raise AuthenticationFailed(GENERIC_LOGIN_ERROR)

        # Successful login — reset the counter. Use self.user (set by
        # TokenObtainPairSerializer.validate() above) rather than the
        # separately-looked-up `user`, since self.user is guaranteed
        # non-None here — cast() tells the type checker the same thing
        # profile_service.py's cast(Users, ...) already does for pk lookups.
        authenticated_user = cast(Users, self.user)
        authenticated_user.failed_login_attempts = 0
        authenticated_user.locked_until = None
        authenticated_user.save(update_fields=['failed_login_attempts', 'locked_until'])
        return data


class CooldownTokenObtainPairView(TokenObtainPairView):
    serializer_class = CooldownTokenObtainPairSerializer
    throttle_classes = [LoginRateThrottle]
