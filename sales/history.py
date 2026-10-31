"""Shared filters for paged history and its full-result summaries."""
from datetime import datetime, time

from django.db.models import F, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from config.api_inputs import parse_positive_id
from sales.models import Transaction


class HistoryFilterError(ValueError):
    def __init__(self, field, message):
        self.field = field
        super().__init__(message)


def filter_history(queryset, params, *, orders=False):
    """Filter single receipt/order rows, without joining their many item rows."""
    prefix = 'transaction__' if orders else ''
    dates = {}
    for field in ('start_date', 'end_date'):
        value = params.get(field)
        try:
            dates[field] = datetime.strptime(value, '%Y-%m-%d').date() if value else None
        except (TypeError, ValueError) as exc:
            raise HistoryFilterError(field, f'{field} must be in YYYY-MM-DD format.') from exc
    start, end = dates['start_date'], dates['end_date']
    if start and end and start > end:
        raise HistoryFilterError('start_date', 'start_date cannot be after end_date.')
    # Orders display the linked receipt's business date; legacy unlinked orders
    # fall back to the order creation date, just as the frontend does.
    date_field = 'created_at'
    if orders:
        queryset = queryset.alias(history_date=Coalesce('transaction__created_at', 'created_at'))
        date_field = 'history_date'
    if start:
        queryset = queryset.filter(**{f'{date_field}__gte': timezone.make_aware(datetime.combine(start, time.min))})
    if end:
        queryset = queryset.filter(**{f'{date_field}__lte': timezone.make_aware(datetime.combine(end, time.max))})

    choices = {
        'payment_method': [value for value, _ in Transaction.PAYMENT_CHOICES],
        'payment_status': ['pending', 'paid', 'unknown'],
    }
    if orders:
        choices['status'] = ['fulfilled', 'cancelled']
    for field, allowed in choices.items():
        value = params.get(field)
        if value:
            if value not in allowed:
                raise HistoryFilterError(field, f'{field} must be one of {", ".join(allowed)}.')
            path = field if field == 'status' else prefix + field
            queryset = queryset.filter(**{path: value})

    for field in ('handled_by', 'customer_id'):
        value = params.get(field)
        if value:
            try:
                value = parse_positive_id(value, field)
            except ValueError as exc:
                raise HistoryFilterError(field, f'{field} must be a valid user id.' if field == 'handled_by'
                                         else 'customer_id must be a valid customer id.') from exc
            queryset = queryset.filter(**{'handled_by_id' if field == 'handled_by' else field: value})

    if not orders and params.get('include_voided', 'false').lower() != 'true':
        queryset = queryset.filter(is_voided=False)

    search = params.get('search', '').strip()
    if len(search) > 200:
        raise HistoryFilterError('search', 'search must contain at most 200 characters.')
    if '\x00' in search:
        raise HistoryFilterError('search', 'search cannot contain null characters.')
    if search:
        matches = (Q(**{prefix + 'source_invoice_number__icontains': search})
                   | Q(customer__name__icontains=search)
                   | Q(handled_by__username__icontains=search))
        if orders:
            try:
                matches |= Q(pk=parse_positive_id(search, 'search'))
            except ValueError:
                pass
        queryset = queryset.filter(matches)
    return queryset


def order_history(queryset, params, *, orders=False):
    prefix = 'transaction__' if orders else ''
    fields = {
        'id': 'id', 'created_at': 'created_at',
        'transaction_date': 'history_date' if orders else 'created_at',
        'transaction_number': prefix + 'source_invoice_number',
        'total_amount': prefix + 'total_amount',
        'customer': 'customer__name', 'handled_by': 'handled_by__username',
    }
    if orders:
        fields['status'] = 'status'
    ordering = params.get('ordering') or '-created_at'
    key = ordering.removeprefix('-')
    if key not in fields:
        raise HistoryFilterError('ordering', f'ordering must be one of {", ".join(fields)}, optionally prefixed with -.')
    descending = ordering.startswith('-')
    if key in ('transaction_number', 'customer') or (orders and key == 'total_amount'):
        expression = F(fields[key])
        expression = expression.desc(nulls_last=True) if descending else expression.asc(nulls_last=True)
    else:
        # Keep the default ORDER BY identical to the existing history index.
        expression = ('-' if descending else '') + fields[key]
    if key == 'id':
        return queryset.order_by(expression)
    return queryset.order_by(expression, '-id' if descending else 'id')
