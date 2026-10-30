"""Readiness for the ASGI listener, without public unauthenticated health data."""
import http.client
import sys


def check():
    probe = http.client.HTTPConnection('127.0.0.1', 8001, timeout=5)
    try:
        probe.request('GET', '/events/', headers={'Host': 'localhost'})
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
