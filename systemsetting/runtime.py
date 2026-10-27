import logging
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from .models import SystemSettings


RUNTIME_SETTINGS_CACHE_KEY = 'systemsetting:runtime:v2'
RUNTIME_SETTINGS_VERSION_KEY = 'systemsetting:runtime:version:v2'
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
    data_key = None
    try:
        version = cache.get(RUNTIME_SETTINGS_VERSION_KEY)
        if version is None:
            cache.add(RUNTIME_SETTINGS_VERSION_KEY, uuid4().hex, timeout=None)
            version = cache.get(RUNTIME_SETTINGS_VERSION_KEY)
        data_key = f'{RUNTIME_SETTINGS_CACHE_KEY}:{version}' if version else None
        data = cache.get(data_key) if data_key else None
    except Exception:
        logger.exception('Runtime-settings cache read failed; using the database')
        data_key = None
        data = None
    if data is not None:
        return {**data, 'timezone': settings.TIME_ZONE}

    config = SystemSettings.get_config()
    data = {
        'system_name': config.system_name,
        'currency': config.currency,
        'date_format': config.date_format,
        'timezone': settings.TIME_ZONE,
        'language': config.language,
        'business_name': config.business_name,
        'business_address': config.business_address,
        'business_contact': config.business_contact,
        'business_email': config.business_email,
        'tin': config.tin,
        'business_type': config.business_type,
        'version': config.updated_at.isoformat(),
    }
    # The namespace was captured before the read. If an admin commits new
    # preferences while this reader is in flight, its old namespace is retired;
    # a late callback cannot overwrite the newer committed preferences.
    def store():
        try:
            cache.set(data_key, data, 300)
        except Exception:
            logger.exception('Runtime-settings cache write failed')
    # Never publish uncommitted settings or cache under an unverified namespace.
    if data_key is not None:
        transaction.on_commit(store)
    return data


def invalidate_runtime_settings(*args, using=None, created=False, **kwargs):
    # Initial singleton creation replaces no committed preferences. Retain the
    # namespace captured by its first reader so the committed result stays warm.
    # Updates and deletions must retire in-flight readers' namespaces.
    if created:
        return
    def invalidate():
        try:
            cache.set(RUNTIME_SETTINGS_VERSION_KEY, uuid4().hex, timeout=None)
        except Exception:
            logger.exception('Runtime-settings cache invalidation failed')
    transaction.on_commit(invalidate, using=using)


def get_brand_name():
    config = get_runtime_settings()
    return config['business_name'] or config['system_name'] or 'Rosario Dairy'
