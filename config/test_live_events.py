"""Live hints must follow commits, preserve access rules and survive reconnects.

Redis integration tests use an explicitly supplied disposable Redis instance:
ROSARIO_TEST_REDIS_URL=redis://127.0.0.1:16379/0 python manage.py test config.test_live_events
"""
import asyncio
from datetime import timedelta
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import OperationalError, connection, transaction
from django.test import Client, RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from config.data_cache import invalidate_data_cache
from config.live_events import events, event_channel, publish_business_change
from inventory.models import Category, Product, ProductBatch
from sales.models import Transaction


TEST_REDIS = os.getenv('ROSARIO_TEST_REDIS_URL', '')
TEST_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                           'LOCATION': 'live-events-tests'}}


class EventFixture:
    def setUp(self):
        super().setUp()
        cache.clear()
        users = get_user_model()
        self.admin = users.objects.create_user(username='live-admin', role='admin', email='live-admin@example.test')
        self.staff = users.objects.create_user(username='live-staff', role='staff', email='live-staff@example.test')
        self.token = AccessToken.for_user(self.admin)
        self.client = Client()

    def request(self, token=None):
        request = RequestFactory().get('/events/', HTTP_AUTHORIZATION=f'Bearer {token or self.token}')
        request.scope = {'type': 'http'}
        return request

    def create_sale_fixture(self):
        category = Category.objects.create(name='Live dairy')
        product = Product.objects.create(name='Live milk', category=category, unit='L', shelf_life=7)
        self.product = product
        self.batch = ProductBatch.objects.create(product=product, batch_number='LIVE-001',
            initial_quantity=10, remaining_quantity=10, unit_price=50,
            expiration_date=timezone.localdate() + timedelta(days=7))

    def sale(self, quantity='2'):
        client = APIClient()
        client.force_authenticate(self.staff)
        return client.post('/sales/checkout/', {
            'items': [{'product_id': self.product.pk, 'quantity': quantity, 'unit_price': '50'}],
            'payment_method': 'online'}, format='json')


@override_settings(CACHES=TEST_CACHE, REDIS_URL='')
class PublicationTests(EventFixture, TransactionTestCase):
    def test_notifications_are_only_published_after_outer_commit(self):
        with patch('config.live_events.publish_business_change') as publish:
            with transaction.atomic():
                with transaction.atomic():
                    invalidate_data_cache()
                publish.assert_not_called()
            publish.assert_called_once()
            self.assertEqual(len(publish.call_args.args[0]), 32)

    def test_rollback_does_not_publish(self):
        with patch('config.live_events.publish_business_change') as publish:
            with self.assertRaises(ValueError):
                with transaction.atomic():
                    invalidate_data_cache()
                    raise ValueError('rollback')
            publish.assert_not_called()

    def test_successful_sale_notifies_and_failed_sale_never_does(self):
        self.create_sale_fixture()
        with patch('config.live_events.publish_business_change') as publish:
            self.assertEqual(self.sale().status_code, 201)
            self.assertTrue(publish.called)
            publish.reset_mock()
            self.assertEqual(self.sale('999').status_code, 400)
            publish.assert_not_called()
        self.assertEqual(Transaction.objects.count(), 1)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, 8)

    def test_publication_failure_never_changes_successful_sale_result(self):
        self.create_sale_fixture()
        with override_settings(REDIS_URL='redis://unreachable.test/0'), \
                patch('config.live_events._publisher', side_effect=ConnectionError('offline')), \
                self.assertLogs('config.live_events', level='ERROR'):
            self.assertEqual(self.sale().status_code, 201)
        self.assertEqual(Transaction.objects.count(), 1)

    def test_cache_rotation_failure_still_attempts_notification_after_commit(self):
        with patch('config.cache_invalidation._rotate', side_effect=ConnectionError), \
                patch('config.live_events.publish_business_change') as publish, \
                self.assertLogs('config.cache_invalidation', level='ERROR'):
            invalidate_data_cache()
        publish.assert_called_once()

    def test_void_and_payment_changes_are_covered_by_existing_write_signals(self):
        from sales.services.order_service import void_transaction
        from sales.services.payment_service import update_encoding_payment
        self.create_sale_fixture()
        self.assertEqual(self.sale().status_code, 201)
        sale = Transaction.objects.get()
        sale.encoded_at = timezone.now()
        sale.save(update_fields=['encoded_at'])
        with patch('config.live_events.publish_business_change') as publish:
            update_encoding_payment(sale.pk, self.admin, payment_status='pending')
            self.assertTrue(publish.called)
            publish.reset_mock()
            void_transaction(sale.pk, voided_by=self.admin, reason='test')
            self.assertTrue(publish.called)


@override_settings(CACHES=TEST_CACHE, REDIS_URL='')
class EventAuthenticationTests(EventFixture, TransactionTestCase):
    def test_missing_query_only_refresh_and_invalid_tokens_return_401(self):
        for header, query in [('', ''), ('', f'?token={self.token}'),
                              ('Bearer invalid', ''), ('Bearer not.a.jwt', '')]:
            response = self.client.get('/events/' + query, HTTP_AUTHORIZATION=header)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response['WWW-Authenticate'], 'Bearer')
        from rest_framework_simplejwt.tokens import RefreshToken
        response = self.client.get('/events/', HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(self.admin)}')
        self.assertEqual(response.status_code, 401)

    def test_expired_revoked_inactive_and_password_changed_sessions_return_401(self):
        expired = AccessToken.for_user(self.admin)
        expired.set_exp(lifetime=timedelta(seconds=-60))
        self.assertEqual(self.client.get('/events/', HTTP_AUTHORIZATION=f'Bearer {expired}').status_code, 401)
        self.admin.token_version += 1
        self.admin.save(update_fields=['token_version'])
        self.assertEqual(self.client.get('/events/', HTTP_AUTHORIZATION=f'Bearer {self.token}').status_code, 401)
        current = AccessToken.for_user(self.admin)
        self.admin.set_password('Changed-test-password!91')
        self.admin.save(update_fields=['password'])
        self.assertEqual(self.client.get('/events/', HTTP_AUTHORIZATION=f'Bearer {current}').status_code, 401)
        current = AccessToken.for_user(self.admin)
        self.admin.is_active = False
        self.admin.save(update_fields=['is_active'])
        self.assertEqual(self.client.get('/events/', HTTP_AUTHORIZATION=f'Bearer {current}').status_code, 401)

    def test_unsupported_role_is_forbidden_and_non_get_is_not_allowed(self):
        self.admin.role = 'other'
        self.admin.save(update_fields=['role'])
        self.assertEqual(self.client.get('/events/', HTTP_AUTHORIZATION=f'Bearer {self.token}').status_code, 403)
        self.assertEqual(self.client.post('/events/').status_code, 405)

    def test_wsgi_and_missing_redis_return_safe_503_instead_of_blocking(self):
        response = self.client.get('/events/', HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Retry-After'], '5')

    def test_database_outage_is_sanitized(self):
        with patch('config.live_events._identity', side_effect=OperationalError('secret database password')), \
                self.assertLogs('config.live_events', level='ERROR'):
            response = self.client.get('/events/', HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret', response.content.decode())


@unittest.skipUnless(TEST_REDIS, 'Requires an explicitly supplied disposable Redis URL')
@override_settings(CACHES=TEST_CACHE, REDIS_URL=TEST_REDIS)
class RedisStreamTests(EventFixture, TransactionTestCase):
    async def connect(self, token=None):
        response = await events(self.request(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store, no-transform')
        self.assertEqual(response['X-Accel-Buffering'], 'no')
        stream = response.streaming_content.__aiter__()
        self.assertEqual(await asyncio.wait_for(anext(stream), 3), b'retry: 3000\n\n')
        ready = await asyncio.wait_for(anext(stream), 3)
        self.assertIn(b'event: ready', ready)
        self.assertIn(b'"resync":true', ready)
        return stream, ready

    def test_sale_reaches_two_viewers_with_coalesced_metadata_only(self):
        self.create_sale_fixture()

        async def scenario():
            streams = []
            try:
                first, _ = await self.connect()
                streams.append(first)
                second, ready = await self.connect(AccessToken.for_user(self.staff))
                streams.append(second)
                self.assertNotIn(b'"users"', ready)
                response = await sync_to_async(self.sale)()
                self.assertEqual(response.status_code, 201)
                changes = await asyncio.gather(*(asyncio.wait_for(anext(s), 4) for s in streams))
                for change in changes:
                    self.assertIn(b'event: data_changed', change)
                    payload = json.loads(change.split(b'data: ')[1])
                    self.assertEqual(set(payload), {'groups', 'version'})
                    for private in ('live-admin', 'live-staff', 'Live milk', '100.00'):
                        self.assertNotIn(private.encode(), change)
            finally:
                for stream in streams:
                    await stream.aclose()
        async_to_sync(scenario)()

    def test_heartbeat_catches_missed_publication_and_reconnect_always_resyncs(self):
        async def scenario():
            first, ready = await self.connect()
            try:
                with patch('config.live_events.publish_business_change'):
                    await sync_to_async(invalidate_data_cache)()
                with patch('config.live_events.HEARTBEAT_SECONDS', 0.05):
                    change = await asyncio.wait_for(anext(first), 2)
                self.assertIn(b'event: data_changed', change)
            finally:
                await first.aclose()
            second, fresh = await self.connect()
            await second.aclose()
            self.assertNotEqual(ready, fresh)
        async_to_sync(scenario)()

    def test_open_connection_stops_after_revocation_role_change_and_expiry(self):
        async def scenario():
            for kind in ('revoke', 'role', 'expiry'):
                token = AccessToken.for_user(self.admin)
                stream, _ = await self.connect(token)
                try:
                    if kind == 'expiry':
                        with patch('config.live_events.time.time', return_value=int(token['exp']) + 1):
                            result = await anext(stream)
                        self.assertIn(b'event: reconnect', result)
                    else:
                        changes = {'token_version': 1} if kind == 'revoke' else {'role': 'staff'}
                        await sync_to_async(get_user_model().objects.filter(pk=self.admin.pk).update)(**changes)
                        with patch('config.live_events.HEARTBEAT_SECONDS', 0.05):
                            result = await asyncio.wait_for(anext(stream), 2)
                        self.assertIn(b'event: auth_required', result)
                finally:
                    await stream.aclose()
                await sync_to_async(get_user_model().objects.filter(pk=self.admin.pk).update)(token_version=0, role='admin')
        async_to_sync(scenario)()

    def test_broker_failure_emits_unavailable_and_closes(self):
        async def scenario():
            stream, _ = await self.connect()
            try:
                with patch('redis.asyncio.client.PubSub.get_message', side_effect=ConnectionError('offline')), \
                        self.assertLogs('config.live_events', level='ERROR'):
                    result = await anext(stream)
                self.assertIn(b'event: unavailable', result)
                with self.assertRaises(StopAsyncIteration):
                    await anext(stream)
            finally:
                await stream.aclose()
        async_to_sync(scenario)()

    def test_broker_connection_failure_after_http_headers_is_a_safe_event(self):
        async def scenario():
            response = await events(self.request())
            stream = response.streaming_content.__aiter__()
            with patch('config.live_events.AsyncRedis.from_url', side_effect=ConnectionError), \
                    self.assertLogs('config.live_events', level='ERROR'):
                self.assertIn(b'event: unavailable', await anext(stream))
            with self.assertRaises(StopAsyncIteration):
                await anext(stream)
        async_to_sync(scenario)()


class PublisherUnitTests(SimpleTestCase):
    @override_settings(REDIS_URL='')
    def test_development_without_redis_does_not_attempt_publication(self):
        with patch('config.live_events._publisher') as publisher:
            publish_business_change('change')
        publisher.assert_not_called()


@unittest.skipUnless(TEST_REDIS, 'Requires an explicitly supplied disposable Redis URL')
@override_settings(CACHES={'default': {
    'BACKEND': 'django.core.cache.backends.redis.RedisCache', 'LOCATION': TEST_REDIS,
}}, REDIS_URL=TEST_REDIS, ALLOWED_HOSTS=['127.0.0.1', 'testserver'])
class RedisHTTPStreamTests(EventFixture, TransactionTestCase):
    def test_separate_asgi_process_delivers_committed_sale_over_http_without_gzip_buffering(self):
        self.assertTrue(connection.settings_dict['NAME'].startswith('test_'))
        self.create_sale_fixture()
        environment = dict(os.environ, DJANGO_ENV='test', DJANGO_DEBUG='false',
            REDIS_URL=TEST_REDIS, DJANGO_ALLOWED_HOSTS='127.0.0.1',
            DJANGO_SETTINGS_MODULE='config.settings', DJANGO_SECURE_SSL_REDIRECT='false')
        for field in ('NAME', 'USER', 'PASSWORD', 'HOST', 'PORT'):
            environment['DB_' + field] = str(connection.settings_dict[field])
        with socket.socket() as listener, tempfile.TemporaryFile() as log:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            url = f'http://127.0.0.1:{listener.getsockname()[1]}/events/'
            process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'config.asgi:application',
                '--fd', str(listener.fileno()), '--lifespan=off', '--no-proxy-headers',
                '--log-level=warning', '--timeout-graceful-shutdown=1'],
                env=environment, pass_fds=(listener.fileno(),), stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        urlopen(url, timeout=0.1).close()
                    except HTTPError as error:
                        self.assertEqual(error.code, 401)
                        break
                    except (URLError, TimeoutError):
                        if process.poll() is not None:
                            self.fail('ASGI subprocess did not start')
                        time.sleep(0.02)
                else:
                    self.fail('ASGI subprocess was not ready')

                def read_event(response):
                    lines = []
                    while True:
                        line = response.readline()
                        if not line:
                            self.fail('Event stream ended before an event')
                        if line == b'\n':
                            block = b''.join(lines)
                            if b'event:' in block:
                                return block
                            lines = []
                        else:
                            lines.append(line)

                request = Request(url, headers={'Authorization': f'Bearer {self.token}',
                    'Accept': 'text/event-stream', 'Accept-Encoding': 'gzip',
                    'Origin': 'http://localhost:5173'})
                with urlopen(request, timeout=5) as stream:
                    self.assertEqual(stream.headers['Content-Encoding'], 'identity')
                    self.assertEqual(stream.headers['Access-Control-Allow-Origin'], 'http://localhost:5173')
                    self.assertIn(b'event: ready', read_event(stream))
                    started = time.monotonic()
                    self.assertEqual(self.sale().status_code, 201)
                    self.assertIn(b'event: data_changed', read_event(stream))
                    self.assertLess(time.monotonic() - started, 2)
                    viewer = APIClient()
                    viewer.force_authenticate(self.admin)
                    self.assertEqual(viewer.get('/sales/transactions/').json()['count'], 1)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
