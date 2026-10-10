"""Reuse rendered JSON for large, already authenticated cached read responses."""
import logging

from django.conf import settings
from rest_framework.renderers import JSONRenderer
from .cache_payloads import get_payload, set_payload

logger = logging.getLogger(__name__)


class CachedJSONRenderer(JSONRenderer):
    def render(self, data, accepted_media_type=None, renderer_context=None):
        context = renderer_context or {}
        response = context.get('response')
        payload_key = getattr(response, 'data_cache_key', None)
        if not payload_key:
            return super().render(data, accepted_media_type, context)
        # Pretty-printed and compact JSON must never reuse each other's body.
        indent = self.get_indent(accepted_media_type, context)
        key = f'{payload_key}:json:{indent}'
        try:
            body = get_payload(key)
        except Exception:
            logger.exception('Rendered JSON cache read failed')
            body = None
        if body is not None:
            return body
        body = super().render(data, accepted_media_type, context)
        try:
            set_payload(key, body, getattr(response, 'data_cache_timeout', settings.API_DATA_CACHE_TIMEOUT))
        except Exception:
            logger.exception('Rendered JSON cache write failed')
        return body
