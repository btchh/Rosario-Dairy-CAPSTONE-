"""Internal loopback probes use an allowed host and the configured proxy scheme."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http.request import validate_host


def healthcheck_headers():
    host = getattr(settings, 'HEALTHCHECK_HOST', '') or next(
        (value for value in settings.ALLOWED_HOSTS if value and '*' not in value), 'localhost')
    if not validate_host(host, settings.ALLOWED_HOSTS):
        raise ImproperlyConfigured('DJANGO_HEALTHCHECK_HOST must be an allowed host.')
    headers = {'Host': host}
    if settings.SECURE_PROXY_SSL_HEADER:
        name, value = settings.SECURE_PROXY_SSL_HEADER
        headers[name.removeprefix('HTTP_').replace('_', '-')] = value
    return headers
