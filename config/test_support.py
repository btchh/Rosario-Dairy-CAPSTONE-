"""Typed boundaries for tests that exclusively call DRF API endpoints.

Django's generic client annotations cannot describe DRF response.data. These
adapters preserve APIClient behavior and specify the response type at the API
boundary; they must not be used for HTML or streaming endpoints.
"""
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, cast

from django.test import TestCase as DjangoTestCase, TransactionTestCase as DjangoTransactionTestCase
from rest_framework.response import Response
from rest_framework.test import APIClient as DRFAPIClient


class APIResponse(Response):
    # JSON response payloads vary by endpoint (objects, lists and error maps).
    data: Any


class APIClient(DRFAPIClient):
    def get(self, path, data=None, follow=False, **extra) -> APIResponse:
        return cast(APIResponse, super().get(path, data=data, follow=follow, **extra))

    def post(self, path, data=None, format=None, content_type=None, follow=False, **extra) -> APIResponse:
        return cast(APIResponse, super().post(path, data=data, format=format,
            content_type=content_type, follow=follow, **extra))

    def put(self, path, data=None, format=None, content_type=None, follow=False, **extra) -> APIResponse:
        return cast(APIResponse, super().put(path, data=data, format=format,
            content_type=content_type, follow=follow, **extra))

    def patch(self, path, data=None, format=None, content_type=None, follow=False, **extra) -> APIResponse:
        return cast(APIResponse, super().patch(path, data=data, format=format,
            content_type=content_type, follow=follow, **extra))

    def delete(self, path, data=None, format=None, content_type=None, follow=False, **extra) -> APIResponse:
        return cast(APIResponse, super().delete(path, data=data, format=format,
            content_type=content_type, follow=follow, **extra))


class APITestCase(DjangoTestCase):
    client_class = APIClient
    client: APIClient

    @classmethod
    def capture_commit_callbacks(cls, *, using='default', execute=False
            ) -> AbstractContextManager[list[Callable[[], object]]]:
        # Some editor stub versions omit this supported Django classmethod.
        capture = getattr(super(), 'captureOnCommitCallbacks')
        return cast(AbstractContextManager[list[Callable[[], object]]],
                    capture(using=using, execute=execute))


class APITransactionTestCase(DjangoTransactionTestCase):
    client_class = APIClient
    client: APIClient
