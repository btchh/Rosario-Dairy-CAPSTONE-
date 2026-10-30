import logging
from asgiref.sync import iscoroutinefunction, markcoroutinefunction, sync_to_async
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone, translation

from .runtime import get_default_runtime_settings, get_runtime_settings


logger = logging.getLogger(__name__)


class RuntimeSettingsMiddleware:
    """Apply database-managed timezone and language for each request."""

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            markcoroutinefunction(self)

    @staticmethod
    def _config():
        try:
            return get_runtime_settings()
        except Exception:
            # Runtime preferences must not turn a transient cache/database
            # problem into a site-wide outage.
            logger.exception('Unable to load runtime settings; using safe defaults')
            return get_default_runtime_settings()

    @staticmethod
    def _activate(config):
        try:
            timezone.activate(ZoneInfo(config['timezone']))
        except ZoneInfoNotFoundError:
            timezone.deactivate()
        translation.activate(config['language'])

    def __call__(self, request):
        if self.is_async:
            return self.__acall__(request)
        self._activate(self._config())
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
            translation.deactivate()

    async def __acall__(self, request):
        self._activate(await sync_to_async(self._config)())
        try:
            return await self.get_response(request)
        finally:
            timezone.deactivate()
            translation.deactivate()
