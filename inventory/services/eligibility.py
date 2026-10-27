"""Shared inventory date eligibility and expiry boundaries."""
from datetime import date, timedelta

from django.db.models import Q
from django.utils import timezone


def expiry_cutoff(today, days):
    """Keep legacy-large thresholds within the supported calendar range."""
    if days >= (date.max - today).days:
        return date.max
    return today + timedelta(days=days)


def expired_stock_filter(today=None):
    today = today or timezone.localdate()
    return (Q(remaining_quantity__gt=0, date_received__lte=today) &
            (Q(status='expired') | Q(status='available', expiration_date__lt=today)))
