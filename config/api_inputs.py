from collections.abc import Mapping
from functools import wraps

from rest_framework import serializers


def object_body(handler):
    """Validate the body before manually parsed views access object fields."""
    @wraps(handler)
    def validated(self, request, *args, **kwargs):
        if not isinstance(request.data, Mapping):
            raise serializers.ValidationError('Request body must be a JSON object.')
        return handler(self, request, *args, **kwargs)
    return validated


def require_item_list(value):
    """Return a JSON item list or raise a user-facing validation error."""
    if not isinstance(value, list) or not value:
        raise ValueError("'items' must be a non-empty list.")
    if any(not isinstance(item, Mapping) for item in value):
        raise ValueError('Each item must be a JSON object.')
    return value


def parse_decimal(value, field_name, *, min_value=None, max_digits=10):
    """Validate finite, database-safe decimal input used by manual API views."""
    field = serializers.DecimalField(
        max_digits=max_digits,
        decimal_places=2,
        min_value=min_value,
        coerce_to_string=False,
    )
    try:
        return field.run_validation(value)
    except serializers.ValidationError as exc:
        raise ValueError(f'{field_name} must be a valid number with at most 2 decimal places.') from exc
