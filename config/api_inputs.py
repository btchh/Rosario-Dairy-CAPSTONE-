from collections.abc import Mapping
from functools import wraps

from rest_framework import serializers
from django.conf import settings


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
    if len(value) > settings.MAX_CART_ITEMS:
        raise ValueError(f'A cart may contain at most {settings.MAX_CART_ITEMS} items.')
    if any(not isinstance(item, Mapping) for item in value):
        raise ValueError('Each item must be a JSON object.')
    seen = set()
    for item in value:
        product_id = item.get('product_id')
        if isinstance(product_id, bool) or not isinstance(product_id, (str, int)):
            raise ValueError('Each item requires a valid product_id.')
        try:
            product_id = int(product_id)
        except ValueError as exc:
            raise ValueError('Each item requires a valid product_id.') from exc
        if product_id <= 0 or product_id > 9223372036854775807:
            raise ValueError('Each item requires a valid product_id.')
        if product_id in seen:
            raise ValueError('Combine quantities for duplicate products before submitting a cart.')
        seen.add(product_id)
    return value


def parse_positive_id(value, field_name):
    """Accept integer IDs and integer strings without lossy ORM coercion."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f'{field_name} must be a valid positive integer id.')
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f'{field_name} must be a valid positive integer id.') from exc
    if not 0 < parsed <= 9223372036854775807:
        raise ValueError(f'{field_name} must be a valid positive integer id.')
    return parsed


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
