# --- accounts/views/login_views.py ---
from datetime import timedelta
from typing import cast
from django.utils import timezone
from accounts.models import Users
from rest_framework.exceptions import Throttled
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

LOGIN_MAX_FAILED_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15


class CooldownTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Wraps the default JWT login serializer with a failed-attempt lockout:
    after LOGIN_MAX_FAILED_ATTEMPTS consecutive failures, the account is
    locked for LOGIN_LOCKOUT_MINUTES. Any successful login resets the
    counter. Locked-out attempts return 429 (Throttled) with a Retry-After
    header rather than the generic 401 used for plain bad credentials, so
    the two cases are distinguishable client-side.
    """

    def validate(self, attrs):
        username = attrs.get(self.username_field)
        user = cast(
            "Users | None",
            Users.objects.filter(**{self.username_field: username}).first(),
        )

        if user is not None and user.locked_until and user.locked_until > timezone.now():
            remaining = int((user.locked_until - timezone.now()).total_seconds())
            raise Throttled(
                wait=remaining,
                detail=(
                    "Account locked due to repeated failed login attempts. "
                    f"Try again in {remaining // 60 + 1} minute(s)."
                ),
            )

        try:
            data = super().validate(attrs)
        except Exception:
            if user is not None:
                user.failed_login_attempts += 1
                if user.failed_login_attempts >= LOGIN_MAX_FAILED_ATTEMPTS:
                    user.locked_until = timezone.now() + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
                user.save(update_fields=['failed_login_attempts', 'locked_until'])
            raise

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