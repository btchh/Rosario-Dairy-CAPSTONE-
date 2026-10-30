"""Authenticated cache-change hints; records still come from permission-checked APIs.

Redis Pub/Sub is only a wakeup mechanism, not a durable transaction log. Every
connection starts with a full resync instruction, and periodic version checks
cover missed publications. Nothing here creates sales or starts forecast jobs.
"""
import asyncio
from functools import lru_cache
import json
import logging
import time

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections
from django.http import JsonResponse, StreamingHttpResponse
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.asyncio.retry import Retry as AsyncRetry
from redis.backoff import NoBackoff
from redis.retry import Retry
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from accounts.authentication import RevocableJWTAuthentication
from .data_cache import data_version


logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 15
MAX_STREAM_SECONDS = 5 * 60
COALESCE_SECONDS = 0.15
COMMON_GROUPS = ["sales", "orders", "inventory", "categories", "customers",
                 "dashboard", "reports", "settings"]


def event_channel():
    # Isolate applications sharing Redis, including separate logical databases.
    from hashlib import sha256
    database = settings.DATABASES['default']
    identity = ':'.join(str(database[field]) for field in ('HOST', 'PORT', 'NAME'))
    return 'rosario:business-events:v1:' + sha256(identity.encode()).hexdigest()[:24]


@lru_cache(maxsize=4)
def _publisher(url):
    return Redis.from_url(url, socket_connect_timeout=0.2, socket_timeout=0.2,
                          retry=Retry(NoBackoff(), 0))


def publish_business_change(change_id):
    """Called only after commit/cache invalidation; failures cannot fail a sale."""
    if not settings.REDIS_URL:
        return
    try:
        _publisher(settings.REDIS_URL).publish(event_channel(), change_id)
    except Exception:
        logger.exception('Live change notification failed; clients will resynchronize')


def _identity(request):
    result = RevocableJWTAuthentication().authenticate(request)
    if result is None:
        raise AuthenticationFailed('An access token is required.')
    user, token = result
    if user.role not in {'admin', 'staff'}:
        raise PermissionDenied('This account cannot receive live updates.')
    return user.pk, user.role, int(token['exp'])


def _state(request, expected=None):
    # Sync ORM work runs briefly in a thread; don't retain a database connection
    # for the lifetime of an idle event stream.
    close_old_connections()
    try:
        identity = _identity(request)
        if expected is not None and identity != expected:
            raise AuthenticationFailed('The account session has changed.')
        return identity, data_version()
    finally:
        close_old_connections()


def frame(event, data):
    return f'event: {event}\ndata: {json.dumps(data, separators=(",", ":"))}\n\n'.encode()


async def change_stream(request, identity):
    client = None
    groups = COMMON_GROUPS + (["users"] if identity[1] == 'admin' else [])
    started = time.monotonic()
    try:
        client = AsyncRedis.from_url(settings.REDIS_URL, socket_connect_timeout=2,
                                    socket_timeout=HEARTBEAT_SECONDS + 3,
                                    retry=AsyncRetry(NoBackoff(), 0))
        async with client.pubsub() as subscription:
            await subscription.subscribe(event_channel())
            # Wait for the subscription acknowledgement before taking the
            # baseline, closing the subscribe/snapshot race.
            ack = await subscription.get_message(timeout=2)
            if not ack or ack['type'] != 'subscribe':
                raise ConnectionError('Live subscription was not acknowledged')
            _, version = await sync_to_async(_state)(request, identity)
            yield b'retry: 3000\n\n'
            yield frame('ready', {'groups': groups, 'version': version, 'resync': True})
            while True:
                remaining = min(MAX_STREAM_SECONDS - (time.monotonic() - started),
                                identity[2] - time.time())
                if remaining <= 0:
                    yield frame('reconnect', {'reason': 'renew_connection'})
                    return
                message = await subscription.get_message(
                    ignore_subscribe_messages=True, timeout=min(HEARTBEAT_SECONDS, remaining))
                if message:
                    # A checkout saves several rows. Merge the burst into one
                    # refresh and bound draining so busy writers cannot starve it.
                    await asyncio.sleep(COALESCE_SECONDS)
                    for _ in range(100):
                        if not await subscription.get_message(ignore_subscribe_messages=True, timeout=0):
                            break
                _, current = await sync_to_async(_state)(request, identity)
                if current != version:
                    version = current
                    yield frame('data_changed', {'groups': groups, 'version': version})
                else:
                    yield b': heartbeat\n\n'
    except (AuthenticationFailed, PermissionDenied):
        yield frame('auth_required', {'reason': 'session_expired_or_changed'})
    except Exception:
        logger.exception('Live event stream interrupted')
        yield frame('unavailable', {'retry_after': 5})
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                logger.exception('Live event connection cleanup failed')


async def events(request):
    if request.method != 'GET':
        response = JsonResponse({'detail': 'Only GET is supported.'}, status=405)
        response['Allow'] = 'GET'
        return response
    try:
        identity, _ = await sync_to_async(_state)(request)
    except AuthenticationFailed:
        response = JsonResponse({'detail': 'A valid access token is required.'}, status=401)
        response['WWW-Authenticate'] = 'Bearer'
        return response
    except PermissionDenied:
        return JsonResponse({'detail': 'Live updates are not permitted.'}, status=403)
    except Exception:
        logger.exception('Live event authentication unavailable')
        return JsonResponse({'detail': 'Live updates are temporarily unavailable.'}, status=503)
    # Refuse WSGI: Django must never consume an endless async iterator eagerly.
    if not hasattr(request, 'scope') or not settings.REDIS_URL:
        response = JsonResponse({'detail': 'Live updates require ASGI and Redis.'}, status=503)
        response['Retry-After'] = '5'
        return response
    response = StreamingHttpResponse(change_stream(request, identity), content_type='text/event-stream')
    response['Cache-Control'] = 'no-store, no-transform'
    # Prevent Django's GZipMiddleware (and outer proxies) from compressing the
    # stream when browsers send Accept-Encoding: gzip.
    response['Content-Encoding'] = 'identity'
    response['X-Accel-Buffering'] = 'no'
    return response
