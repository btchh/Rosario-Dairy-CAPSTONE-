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


class TokenRefreshRateThrottle(ClientIPRateThrottle):
    scope = 'token_refresh'


class AccountRateThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        ident = str(request.user.pk) if request.user.is_authenticated else self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class EmailChangeRateThrottle(AccountRateThrottle):
    scope = 'email_change'


class ProfileRateThrottle(AccountRateThrottle):
    scope = 'profile'


class ReportRefreshRateThrottle(AccountRateThrottle):
    scope = 'report_refresh'


class ExpensiveReadRateThrottle(AccountRateThrottle):
    scope = 'expensive_read'


class SaleWriteRateThrottle(AccountRateThrottle):
    scope = 'sale_write'
