"""Shared definition of expired physical stock awaiting a write-off."""
from django.db.models import Q
from django.utils import timezone


def expired_stock_filter(today=None):
    today = today or timezone.localdate()
    return (Q(remaining_quantity__gt=0, date_received__lte=today) &
            (Q(status='expired') | Q(status='available', expiration_date__lt=today)))
