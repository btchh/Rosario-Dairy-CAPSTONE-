from rest_framework.throttling import SimpleRateThrottle


class ClientIPRateThrottle(SimpleRateThrottle):
    """Apply a scoped IP limit even when a caller includes a valid JWT."""

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request),
        }


class LoginRateThrottle(ClientIPRateThrottle):
    scope = 'login'


class PasswordResetRequestRateThrottle(ClientIPRateThrottle):
    scope = 'password_reset_request'


class PasswordResetConfirmRateThrottle(ClientIPRateThrottle):
    scope = 'password_reset_confirm'
