"""Read verified historical sales and completed live sales for forecasting."""
from datetime import datetime, time
from decimal import Decimal
from hashlib import sha256
import json

from django.db import connection
from django.db.models import Q, Sum, Count
from django.db.models.functions import TruncDate
from django.utils import timezone
from sales.models import Transaction


def source(scope='real'):
    if scope != 'real':
        raise ValueError('Only real sales are supported')
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
        today = timezone.localdate()
        daily = {}
        if 'reporting_historicalsalesday' in tables:
            cursor.execute(
                'SELECT date,revenue FROM reporting_historicalsalesday WHERE date<%s ORDER BY date',
                [today],
            )
            daily = dict(cursor.fetchall())
        cutoff = timezone.make_aware(datetime.combine(today, time.min))
        live = Transaction.objects.filter(created_at__lt=cutoff).annotate(
            day=TruncDate('created_at', tzinfo=timezone.get_current_timezone()),
        ).values('day').annotate(
            revenue=Sum('total_amount', filter=Q(is_voided=False), default=Decimal('0.00')),
            count=Count('pk'),
        ).order_by('day')
        for row in live:
            if row['day'] in daily:
                raise ValueError('Imported and live revenue overlap; resolve before training')
            daily[row['day']] = row['revenue']
        warnings = ['Imported raw/dairy coverage must match live product/accounting coverage.'] if 'reporting_historicalsalesday' in tables else []
    signature = sha256(json.dumps({
        'scope': scope,
        'timezone': timezone.get_current_timezone_name(),
        'daily': [(str(day), str(revenue)) for day, revenue in sorted(daily.items())],
    }, sort_keys=True, default=str).encode()).hexdigest()
    return daily, signature, warnings


def as_series(daily):
    import pandas as pd
    return pd.Series(
        [float(value) for _, value in sorted(daily.items())],
        index=pd.DatetimeIndex([day for day, _ in sorted(daily.items())]),
        dtype=float,
    )
