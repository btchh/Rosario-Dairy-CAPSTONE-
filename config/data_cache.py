"""Cache authenticated read payloads; invalidate after business writes commit."""
from functools import wraps
from hashlib import sha256
import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.utils import timezone
from rest_framework.response import Response
from .cache_payloads import get_payload, set_payload
from .cache_invalidation import cache_namespace, invalidate_namespace

logger = logging.getLogger(__name__)
VERSION_KEY = 'business-data:version:v1'
NAMESPACE = 'business-data'


def staff_visibility_version():
    """Check current access boundaries even if cache invalidation was lost.

    Never cache this digest: a cache outage must not revive a hidden category
    or a product moved into one. Database errors propagate rather than serving
    a payload whose visibility cannot be verified.
    """
    from inventory.models import Category, Product
    boundaries = (
        list(Category.objects.order_by('pk').values_list('pk', 'is_active', 'is_visible_to_staff')),
        list(Product.objects.order_by('pk').values_list('pk', 'category_id', 'is_active')),
    )
    return sha256(json.dumps(boundaries).encode()).hexdigest()


def data_version(visible_to_staff=False):
    visibility = staff_visibility_version() if visible_to_staff else None
    version = cache_namespace(NAMESPACE, VERSION_KEY)
    return sha256(f'{version}:{visibility}'.encode()).hexdigest() if visibility else version


def invalidate_data_cache(using=None):
    invalidate_namespace(NAMESPACE, VERSION_KEY, using=using)


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
            data_version(visible_to_staff=request.user.role == 'staff'), request.user.pk, request.user.role,
            request.build_absolute_uri(), timezone.get_current_timezone_name(),
            str(timezone.localdate()),
        )
        # v4 removes retired pricing fields from cached response contracts.
        key = 'api-data:v4:' + sha256(json.dumps(identity).encode()).hexdigest()
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
