from rest_framework.throttling import SimpleRateThrottle
from rest_framework.exceptions import APIException
from redis.exceptions import RedisError


class RateLimitUnavailable(APIException):
    status_code = 503
    default_detail = 'Request rate limiting is temporarily unavailable. Try again shortly.'
    default_code = 'rate_limit_unavailable'


class CacheBackedRateThrottle(SimpleRateThrottle):
    """Fail closed with a safe availability response when the cache is down."""

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except (OSError, RedisError) as exc:
            raise RateLimitUnavailable() from exc


class ClientIPRateThrottle(CacheBackedRateThrottle):
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


class AccountRateThrottle(CacheBackedRateThrottle):
    def get_cache_key(self, request, view):
        ident = str(request.user.pk) if request.user.is_authenticated else self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class EmailChangeRateThrottle(AccountRateThrottle):
    scope = 'email_change'


class ProfileRateThrottle(AccountRateThrottle):
    scope = 'profile'


class ReportRefreshRateThrottle(AccountRateThrottle):
    scope = 'report_refresh'


class ReportPreviewRateThrottle(AccountRateThrottle):
    scope = 'report_preview'


class ReportExportRateThrottle(AccountRateThrottle):
    scope = 'report_export'


class ForecastRefreshRateThrottle(AccountRateThrottle):
    scope = 'forecast_refresh'


class ExpensiveReadRateThrottle(AccountRateThrottle):
    scope = 'expensive_read'


class SaleWriteRateThrottle(AccountRateThrottle):
    scope = 'sale_write'
