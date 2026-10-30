"""Readiness for the ASGI listener, without public unauthenticated health data."""
import http.client
import os
from pathlib import Path
import sys


def check():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from config.health import healthcheck_headers

    probe = http.client.HTTPConnection('127.0.0.1', 8001, timeout=5)
    try:
        probe.request('GET', '/events/', headers=healthcheck_headers())
        if probe.getresponse().status != 401:
            raise RuntimeError('Live events probe failed')
    finally:
        probe.close()


if __name__ == '__main__':
    try:
        check()
    except Exception:
        print('Live events health check failed.', file=sys.stderr)
        raise SystemExit(1)
