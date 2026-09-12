import logging

from django.conf import settings
from django.core.cache import cache

from .models import SystemSettings


RUNTIME_SETTINGS_CACHE_KEY = 'systemsetting:runtime:v1'
logger = logging.getLogger(__name__)


def get_default_runtime_settings():
    """Safe values used while the settings database/cache is unavailable."""
    return {
        'system_name': 'Rosario Dairy',
        'currency': 'PHP',
        'date_format': 'MM/DD/YYYY',
        'timezone': settings.TIME_ZONE,
        'language': settings.LANGUAGE_CODE,
        'business_name': '',
        'business_address': '',
        'business_contact': '',
        'business_email': '',
        'tin': '',
        'business_type': '',
        'version': 'fallback',
    }


def get_runtime_settings():
    try:
        data = cache.get(RUNTIME_SETTINGS_CACHE_KEY)
    except Exception:
        logger.exception('Runtime-settings cache read failed; using the database')
        data = None
    if data is not None:
        return data

    config = SystemSettings.get_config()
    data = {
        'system_name': config.system_name,
        'currency': config.currency,
        'date_format': config.date_format,
        'timezone': config.timezone,
        'language': config.language,
        'business_name': config.business_name,
        'business_address': config.business_address,
        'business_contact': config.business_contact,
        'business_email': config.business_email,
        'tin': config.tin,
        'business_type': config.business_type,
        'version': config.updated_at.isoformat(),
    }
    try:
        cache.set(RUNTIME_SETTINGS_CACHE_KEY, data, 300)
    except Exception:
        logger.exception('Runtime-settings cache write failed')
    return data


def invalidate_runtime_settings(*args, **kwargs):
    try:
        cache.delete(RUNTIME_SETTINGS_CACHE_KEY)
    except Exception:
        logger.exception('Runtime-settings cache invalidation failed')


def get_brand_name():
    config = get_runtime_settings()
    return config['business_name'] or config['system_name'] or 'Rosario Dairy'
