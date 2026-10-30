"""Durable pending changes keep old cache namespaces from becoming valid again."""
from hashlib import sha256
import logging
from uuid import uuid4

from django.core.cache import cache
from django.db import DEFAULT_DB_ALIAS, DatabaseError, connections, transaction

from .models import CacheInvalidation


logger = logging.getLogger(__name__)
RECOVERY_BATCH_SIZE = 1000


def _rotate(version_key):
    version = uuid4().hex
    if cache.set(version_key, version, timeout=None) is False:
        raise ConnectionError('Cache refused the namespace update.')
    return version


def _remove_pending(ids, using):
    try:
        # Cleanup failures must not turn an already committed sale into an error.
        with transaction.atomic(using=using):
            CacheInvalidation.objects.using(using).filter(pk__in=ids).delete()
    except DatabaseError:
        logger.exception('Cache invalidation cleanup failed; retaining pending changes')


def invalidate_namespace(namespace, version_key, *, using=None):
    using = using or DEFAULT_DB_ALIAS
    pending = CacheInvalidation.objects.using(using).create(namespace=namespace)

    def invalidate():
        try:
            _rotate(version_key)
        except Exception:
            logger.exception('Cache invalidation failed; retaining pending change')
        else:
            _remove_pending([pending.pk], using)
        if namespace == 'business-data':
            from .live_events import publish_business_change
            publish_business_change(pending.pk.hex)

    transaction.on_commit(invalidate, using=using)


def cache_namespace(namespace, version_key, *, using=None):
    """Check durable changes before trusting shared or process-local payloads.

    Never drain markers from an open transaction: they may represent this
    reader's uncommitted writes and must remain visible until commit/rollback.
    Unique marker IDs also isolate any payload built from uncommitted data.
    """
    using = using or DEFAULT_DB_ALIAS
    pending = list(CacheInvalidation.objects.using(using).filter(
        namespace=namespace).order_by('pk').values_list('pk', flat=True)[:RECOVERY_BATCH_SIZE + 1])
    # Database errors deliberately propagate: an unverified cache is not fresh.
    # Bound both the guard read and cleanup after a prolonged outage. Large
    # queues use unique namespaces until recovery drains them in batches.
    digest = (uuid4().hex if len(pending) > RECOVERY_BATCH_SIZE else
              sha256(''.join(value.hex for value in pending).encode()).hexdigest())
    pending = pending[:RECOVERY_BATCH_SIZE]
    try:
        if pending and not connections[using].in_atomic_block:
            version = _rotate(version_key)
            # Delete only the exact set observed before rotation. A concurrent
            # writer's later marker must survive until its own invalidation.
            _remove_pending(pending, using)
        else:
            version = cache.get(version_key)
            if version is None:
                cache.add(version_key, uuid4().hex, timeout=None)
                version = cache.get(version_key)
        version = version or uuid4().hex
    except Exception:
        logger.exception('Cache namespace unavailable; using a fresh read namespace')
        version = uuid4().hex
    return sha256(f'{version}:{digest}'.encode()).hexdigest()
