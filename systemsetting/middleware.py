from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone, translation

from .runtime import get_runtime_settings


class RuntimeSettingsMiddleware:
    """Apply database-managed timezone and language for each request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        config = get_runtime_settings()
        try:
            timezone.activate(ZoneInfo(config['timezone']))
        except ZoneInfoNotFoundError:
            timezone.deactivate()
        translation.activate(config['language'])
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
            translation.deactivate()
