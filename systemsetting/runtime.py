from django.core.cache import cache

from .models import SystemSettings


RUNTIME_SETTINGS_CACHE_KEY = 'systemsetting:runtime:v1'


def get_runtime_settings():
    data = cache.get(RUNTIME_SETTINGS_CACHE_KEY)
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
    cache.set(RUNTIME_SETTINGS_CACHE_KEY, data, 300)
    return data


def invalidate_runtime_settings(*args, **kwargs):
    cache.delete(RUNTIME_SETTINGS_CACHE_KEY)


def get_brand_name():
    config = get_runtime_settings()
    return config['business_name'] or config['system_name'] or 'Rosario Dairy'
