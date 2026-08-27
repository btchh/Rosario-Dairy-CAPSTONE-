from datetime import datetime
from django.utils import timezone
# Batch Number Generator

def generate_batch_number(prefix, sequence):
    """
    Generate a unique batch number for an ingredient / product based on its name and the current date.
    The format is: PRD-2506-001 / ING-2506-001
    """

    now = timezone.now()
    year = now.strftime("%y")  # Get last two digits of the year
    month = now.strftime("%m")  # Get the month in two digits
    seq = f"{sequence:03d}"  # Format sequence as a three-digit number with leading zeros
    return f"{prefix}-{year}{month}-{seq}" # Return the formatted batch number


def to_date(value):
    """
    Normalizes a date/datetime value to a plain date. DateField's
    default=timezone.now (used by date_received) returns an aware datetime,
    and Django doesn't coerce that to a date on the in-memory instance until
    it round-trips through the DB — a freshly created instance that never
    specified date_received can still be holding the raw datetime. Mirrors
    what Django's own DateField.to_python() does on save, so behavior stays
    consistent whether the value came from a fresh instance or a DB fetch.
    datetime is a subclass of date, so this check order matters.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.date()
    return value