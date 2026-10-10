"""Keep versioned payloads hot in memory while retaining a shared disk/Redis copy.

The request cache key contains the current shared business-data version. That
version is always read from the shared cache, so another process's committed
writes invalidate memory entries too. Store the original expiry alongside the
value so loading an existing shared entry never extends its lifetime.
"""
import time

from django.core.cache import cache
from django.core.cache.backends.locmem import LocMemCache


_memory = LocMemCache('business-payloads', {'OPTIONS': {'MAX_ENTRIES': 128}})
_missing = object()


def get_payload(key):
    entry = _memory.get(key, _missing)
    if entry is _missing:
        entry = cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        remaining = None if expires_at is None else expires_at - time.time()
        if remaining is not None and remaining <= 0:
            return None
        _memory.set(key, entry, remaining)
        return value
    expires_at, value = entry
    if expires_at is not None and expires_at <= time.time():
        _memory.delete(key)
        return None
    return value


def set_payload(key, value, timeout):
    expires_at = None if timeout is None else time.time() + timeout
    entry = (expires_at, value)
    cache.set(key, entry, timeout)
    remaining = None if expires_at is None else max(0, expires_at - time.time())
    _memory.set(key, entry, remaining)
