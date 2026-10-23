"""Safe API availability errors; do not retry database mutations automatically."""
import logging

from django.db import InterfaceError, OperationalError
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class DatabaseUnavailable(APIException):
    status_code = 503
    default_detail = 'Database service is temporarily unavailable. Check transaction status before retrying a submitted sale.'
    default_code = 'database_unavailable'


def api_exception_handler(exc, context):
    if isinstance(exc, (InterfaceError, OperationalError)):
        logger.error('Database operation failed during an API request', exc_info=exc)
        exc = DatabaseUnavailable()
    return exception_handler(exc, context)
