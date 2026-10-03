"""Read completed, non-voided transaction revenue for forecasting."""
from datetime import datetime, time
from hashlib import sha256
import json

from decimal import Decimal

from django.db.models import Case, CharField, Q, Sum, Value, When
from django.db.models.functions import TruncDate
from django.utils import timezone
from sales.models import Transaction


def source(scope='real', include_components=False):
    if scope != 'real':
        raise ValueError('Only real sales are supported')
    cutoff = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    # Imported labels are sale-time evidence. Live sales use their linked
    # customer name only when there is no imported label.
    feeding = Q(source_customer_label__icontains='feeding') | (
        Q(source_customer_label='') & Q(customer__name__icontains='feeding'))
    rows = Transaction.objects.filter(is_voided=False, created_at__lt=cutoff).annotate(
        day=TruncDate('created_at', tzinfo=timezone.get_current_timezone()),
        segment=Case(When(feeding, then=Value('feeding')), default=Value('other_sales'), output_field=CharField()),
    ).values('day', 'segment').annotate(revenue=Sum('total_amount')).order_by('day', 'segment')
    daily = {}
    components = {'feeding': {}, 'other_sales': {}}
    for row in rows:
        day, revenue = row['day'], row['revenue']
        daily[day] = daily.get(day, Decimal('0.00')) + revenue
        components[row['segment']][day] = revenue
    warnings = []
    signature = sha256(json.dumps({
        'scope': scope,
        'timezone': timezone.get_current_timezone_name(),
        'daily': [(str(day), str(revenue)) for day, revenue in sorted(daily.items())],
        'components': {key: [(str(day), str(value)) for day, value in sorted(values.items())]
                       for key, values in components.items()},
    }, sort_keys=True, default=str).encode()).hexdigest()
    if include_components:
        return daily, signature, warnings, components
    return daily, signature, warnings


def as_series(daily):
    import pandas as pd
    return pd.Series(
        [float(value) for _, value in sorted(daily.items())],
        index=pd.DatetimeIndex([day for day, _ in sorted(daily.items())]),
        dtype=float,
    )
