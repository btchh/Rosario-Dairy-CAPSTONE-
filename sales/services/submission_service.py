"""Optional idempotency for legacy sale routes without changing unkeyed callers."""
from hashlib import sha256
import json

from django.db import connection, transaction
from rest_framework import serializers
from rest_framework.exceptions import APIException

from sales.models import SaleSubmission


class SubmissionConflict(APIException):
    status_code = 409
    default_detail = 'This request ID was already used for a different sale. Use a new request ID.'


def request_id(request):
    """Accept either a UUID body field or UUID Idempotency-Key header."""
    field = serializers.UUIDField()
    body = request.data.get('request_id')
    header = request.headers.get('Idempotency-Key')
    body = field.run_validation(body) if body is not None else None
    header = field.run_validation(header) if header is not None else None
    if body is not None and header is not None and body != header:
        raise serializers.ValidationError({'request_id': 'request_id and Idempotency-Key must match.'})
    return body if body is not None else header


def submit_sale(*, user, key, endpoint, items, customer, payment_method, amount_tendered, create):
    if key is None:
        return create(), True
    payload = {
        'endpoint': endpoint, 'customer_id': customer.pk if customer else None,
        'payment_method': payment_method, 'amount_tendered': amount_tendered,
        'items': [(product.pk, *values) for product, *values in items],
    }
    fingerprint = sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    with transaction.atomic():
        # Acquire the replay lock before FEFO locks, including when the first
        # request uses the very last stock. Rollback releases the key for retry.
        if connection.vendor == 'postgresql':
            lock_key = int.from_bytes(sha256(f'sale:{user.pk}:{key}'.encode()).digest()[:8], 'big', signed=True)
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_key])
        previous = SaleSubmission.objects.select_related('transaction', 'order').filter(user=user, request_id=key).first()
        if previous is not None:
            if previous.fingerprint != fingerprint or previous.endpoint != endpoint:
                raise SubmissionConflict()
            return (previous.order if endpoint == 'order' else previous.transaction), False
        result = create()
        SaleSubmission.objects.create(user=user, request_id=key, fingerprint=fingerprint, endpoint=endpoint,
            transaction=result.transaction if endpoint == 'order' else result,
            order=result if endpoint == 'order' else None)
        return result, True
