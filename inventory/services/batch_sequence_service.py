from django.db import IntegrityError
from django.db import transaction as db_transaction
from django.utils import timezone
from ..models import BatchSequence


def next_sequence(prefix):
    """
    Atomically returns the next sequence number for a batch-number prefix
    ('PRD' or 'ING') within the current year/month. select_for_update() on
    the (already-existing) sequence row is what actually serializes two
    concurrent batch creations — the get_or_create() on the very first batch
    of a given month/prefix can itself race (nothing exists yet to lock), so
    that specific case is handled with a small retry on IntegrityError
    rather than a lock.
    """
    now = timezone.now()
    year, month = now.year, now.month

    for attempt in range(3):
        try:
            with db_transaction.atomic():
                seq_row, _ = BatchSequence.objects.select_for_update().get_or_create(
                    prefix=prefix, year=year, month=month, defaults={'last_seq': 0}
                )
                seq_row.last_seq += 1
                seq_row.save(update_fields=['last_seq'])
                return seq_row.last_seq
        except IntegrityError:
            if attempt == 2:
                raise
            continue