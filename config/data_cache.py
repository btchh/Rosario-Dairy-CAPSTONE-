"""Cache authenticated read payloads; invalidate after business writes commit."""
from functools import wraps
from hashlib import sha256
import json
import logging
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.utils import timezone
from rest_framework.response import Response
from .cache_payloads import get_payload, set_payload

logger = logging.getLogger(__name__)
VERSION_KEY = 'business-data:version:v1'


def data_version():
    try:
        version = cache.get(VERSION_KEY)
        if version is None:
            cache.add(VERSION_KEY, uuid4().hex, timeout=None)
            version = cache.get(VERSION_KEY)
        return version or uuid4().hex
    except Exception:
        logger.exception('Data cache unavailable; reading directly')
        return uuid4().hex


def invalidate_data_cache(using=None):
    def invalidate():
        try:
            cache.set(VERSION_KEY, uuid4().hex, timeout=None)
        except Exception:
            logger.exception('Data cache invalidation failed')
    transaction.on_commit(invalidate, using=using)


def _business_write(sender, using, update_fields=None, **kwargs):
    if (sender._meta.label_lower == settings.AUTH_USER_MODEL.lower()
            and update_fields is not None
            and set(update_fields).issubset({
                'last_login', 'failed_login_attempts', 'locked_until',
                'password', 'last_password_change_at',
            })):
        # Authentication still checks the current account on every request.
        # Login counters/password timestamps don't change history payloads.
        return
    invalidate_data_cache(using=using)


def connect_invalidation_signals():
    from django.apps import apps
    for model in apps.get_models():
        if (model._meta.app_label in {'inventory', 'sales', 'forecasting'}
                or model._meta.label_lower in {
                    settings.AUTH_USER_MODEL.lower(),
                    'systemsetting.systemsettings', 'systemsetting.notificationsettings',
                }):
            for name, signal in (('save', post_save), ('delete', post_delete)):
                signal.connect(_business_write, sender=model,
                               dispatch_uid=f'data-cache:{name}:{model._meta.label_lower}')


def cache_api_response(handler):
    """Runs inside DRF's handler, after authentication and view permissions.

    Only list/aggregate handlers use this: detail handlers must still execute
    object permission checks. Cache data, never rendered responses or headers.
    """
    @wraps(handler)
    def wrapped(self, request, *args, **kwargs):
        if request.method not in {'GET', 'HEAD'} or not request.user.is_authenticated:
            return handler(self, request, *args, **kwargs)
        identity = (
            data_version(), request.user.pk, request.user.role,
            request.build_absolute_uri(), timezone.get_current_timezone_name(),
            str(timezone.localdate()),
        )
        # v3 stores shared payloads together with their original expiry.
        key = 'api-data:v3:' + sha256(json.dumps(identity).encode()).hexdigest()
        timeout = getattr(self, 'data_cache_timeout', settings.API_DATA_CACHE_TIMEOUT)
        try:
            payload = get_payload(key)
        except Exception:
            logger.exception('API cache read failed')
            payload = None
        if payload is not None:
            response = Response(payload)
            response.data_cache_key = key
            response.data_cache_timeout = timeout
            response['X-Data-Cache'] = 'HIT'
            return response
        response = handler(self, request, *args, **kwargs)
        if response.status_code == 200:
            response.data_cache_key = key
            response.data_cache_timeout = timeout
            response['X-Data-Cache'] = 'MISS'
            try:
                set_payload(key, response.data, timeout)
            except Exception:
                logger.exception('API cache write failed')
        return response
    return wrapped


class CachedListMixin:
    @cache_api_response
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
