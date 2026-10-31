"""Deduplicate keyed stock adjustments before acquiring the stock row lock."""
from decimal import Decimal
from hashlib import sha256
import json

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.renderers import JSONRenderer

from config.api_inputs import UUIDTextField
from inventory.models import StockAdjustmentSubmission
from inventory.serializers import StockAdjustmentSerializer
from inventory.services.batch_service import BatchService


class AdjustmentSubmissionConflict(APIException):
    status_code = 409
    default_detail = 'This request ID was already used for a different stock adjustment.'
    default_code = 'idempotency_conflict'


def adjustment_request_id(request):
    """Accept an optional UUID in the JSON body or Idempotency-Key header."""
    keys = []
    for field, source in (('request_id', request.data), ('Idempotency-Key', request.headers)):
        if field in source:
            try:
                keys.append(UUIDTextField().run_validation(source[field]))
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({field: exc.detail}) from exc
    if len(keys) == 2 and keys[0] != keys[1]:
        raise serializers.ValidationError({'request_id': 'request_id and Idempotency-Key must match.'})
    return keys[0] if keys else None


def submit_stock_adjustment(*, key, **values):
    if key is None:
        adjustment = BatchService.create_stock_adjustment(**values)
        return StockAdjustmentSerializer(adjustment).data, True

    user = values['adjusted_by']
    payload = {
        'product_batch_id': values['product_batch'].pk,
        'adjustment_type': values['adjustment_type'],
        'quantity': format(values['quantity'] or Decimal(0), '.2f'),
        'unit_cost': format(values['unit_cost'] or Decimal(0), '.2f'),
        'reason': values['reason'] or '',
    }
    fingerprint = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        # Lock the logical request before stock, even if no replay row exists
        # yet. A rollback releases the key and all stock/audit changes together.
        if connection.vendor == 'postgresql':
            lock_key = int.from_bytes(sha256(
                f'stock-adjustment:{user.pk}:{key}'.encode()).digest()[:8], 'big', signed=True)
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [lock_key])
        else:
            # Row-locking databases can serialize callers by account instead.
            get_user_model().objects.select_for_update().get(pk=user.pk)
        previous = StockAdjustmentSubmission.objects.filter(user=user, request_id=key).first()
        if previous is not None:
            if previous.fingerprint != fingerprint:
                raise AdjustmentSubmissionConflict()
            return previous.response_data, False

        adjustment = BatchService.create_stock_adjustment(**values)
        # Match the wire response, including computed Decimal stock totals,
        # rather than storing Python-only serializer values in JSONField.
        response_data = json.loads(JSONRenderer().render(StockAdjustmentSerializer(adjustment).data))
        StockAdjustmentSubmission.objects.create(
            user=user, request_id=key, fingerprint=fingerprint,
            adjustment=adjustment, response_data=response_data)
        return response_data, True
