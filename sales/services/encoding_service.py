"""Record backdated sales through the same inventory, order and receipt pipeline."""
from datetime import date, datetime, time
from hashlib import sha256
import json

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from sales.models import Order, Transaction
from . import checkout_service, order_service
from .item_pricing import price_items


class EncodingConflict(ValueError):
    pass


def _replayed(existing, user, fingerprint):
    if existing.handled_by_id != user.pk or existing.encoding_payload_hash != fingerprint:
        raise EncodingConflict('This request_id has already been used. Use a new request_id for a different transaction.')
    return existing, False


def encode_transaction(*, items, handled_by, transaction_date, request_id,
                       customer=None, si_number='', note='', payment_method='cash',
                       amount_tendered=None, payment_status='pending'):
    """Return (transaction, created). A retry never deducts inventory twice."""
    if not isinstance(transaction_date, date) or isinstance(transaction_date, datetime):
        raise ValueError('transaction_date must be a date.')
    if transaction_date > timezone.localdate():
        raise ValueError('Transaction date cannot be in the future.')
    if request_id is None:
        raise ValueError('request_id is required.')
    lines = price_items(items, handled_by)
    fingerprint = sha256(json.dumps({
        'date': transaction_date.isoformat(), 'customer': customer.pk if customer else None,
        'si_number': si_number, 'note': note, 'payment_method': payment_method, 'payment_status': payment_status,
        'amount_tendered': str(amount_tendered) if amount_tendered is not None else None,
        'items': [(line.product.pk, str(line.quantity), str(line.unit_price),
                   'none', '0.00') for line in lines],
    }, sort_keys=True).encode()).hexdigest()
    existing = Transaction.objects.filter(encoding_request_id=request_id).first()
    if existing:
        return _replayed(existing, handled_by, fingerprint)

    try:
        with transaction.atomic():
            # Serialize matching requests before stock deduction. A concurrent
            # retry must still succeed when the first request used the last stock.
            if connection.vendor == 'postgresql':
                lock_key = int.from_bytes(sha256(str(request_id).encode()).digest()[:8], 'big', signed=True)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_key])
            existing = Transaction.objects.filter(encoding_request_id=request_id).first()
            if existing:
                return _replayed(existing, handled_by, fingerprint)
            if customer:
                order = order_service.place_order(
                    customer, items, handled_by, payment_method=payment_method,
                    amount_tendered=amount_tendered, payment_status=payment_status,
                )
                sale = order.transaction
            else:
                sale = checkout_service.checkout(
                    items, handled_by, payment_method=payment_method,
                    amount_tendered=amount_tendered, payment_status=payment_status,
                )
            entered_at = timezone.now()
            sale.created_at = (entered_at if transaction_date == timezone.localdate(entered_at)
                               else timezone.make_aware(datetime.combine(transaction_date, time(12))))
            sale.encoded_at = entered_at
            sale.encoding_request_id = request_id
            sale.encoding_payload_hash = fingerprint
            sale.source_invoice_number = si_number
            sale.source_note = note
            sale.source_customer_label = customer.name if customer else ''
            sale.save(update_fields=[
                'created_at', 'encoded_at', 'encoding_request_id', 'encoding_payload_hash',
                'source_invoice_number', 'source_note', 'source_customer_label',
            ])
            # Orders use the same business date; updated_at retains actual encoding time.
            Order.objects.filter(transaction=sale).update(created_at=sale.created_at)
            return sale, True
    except IntegrityError:
        # A concurrent submission may have committed the same request_id while
        # this request waited for stock locks. Its duplicate stock/order writes
        # have rolled back before the winning receipt is returned.
        existing = Transaction.objects.filter(encoding_request_id=request_id).first()
        if existing:
            return _replayed(existing, handled_by, fingerprint)
        raise
