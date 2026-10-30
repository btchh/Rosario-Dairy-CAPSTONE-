"""Check database, cache and the API process without adding a public endpoint."""
import http.client
import os
from pathlib import Path
import sys
from uuid import uuid4


def check():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()

    from django.core.cache import cache
    from django.db import connection
    from config.health import healthcheck_headers

    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        if cursor.fetchone() != (1,):
            raise RuntimeError('Database probe failed')
    key = 'docker-health:' + uuid4().hex
    try:
        cache.set(key, key, timeout=10)
        if cache.get(key) != key:
            raise RuntimeError('Cache probe failed')
    finally:
        cache.delete(key)
        connection.close()

    probe = http.client.HTTPConnection('127.0.0.1', 8000, timeout=5)
    try:
        # An unauthenticated user read returns 401 without consuming login throttles.
        probe.request('GET', '/accounts/user/', headers=healthcheck_headers())
        if probe.getresponse().status != 401:
            raise RuntimeError('API probe failed')
    finally:
        probe.close()


if __name__ == '__main__':
    try:
        check()
    except Exception:
        print('Backend health check failed.', file=sys.stderr)
        raise SystemExit(1)
