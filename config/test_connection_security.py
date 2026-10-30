"""Availability failures must not bypass throttles or run business mutations."""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import InterfaceError, OperationalError
from django.test import SimpleTestCase, override_settings
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from config.test_support import APITestCase
from sales.models import Transaction
from forecasting.models import ForecastRefreshJob
from accounts.models import PasswordResetChallenge


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class RateLimitConnectionTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(username='connection-audit',
            email='connection@example.test', password='StrongAuditPass!92', role='admin')

    def test_public_login_and_recovery_fail_closed_on_cache_read_or_write_failure(self):
        endpoints = [('/accounts/login/', {'username': self.admin.username, 'password': 'StrongAuditPass!92'}),
                     ('/accounts/forgot-password/', {'username': self.admin.username, 'email': self.admin.email}),
                     ('/accounts/reset-password/', {'username': self.admin.username, 'email': self.admin.email,
                         'otp': '123456', 'new_password': 'DifferentAuditPass!29'})]
        for operation in ('get', 'set'):
            for url, data in endpoints:
                cache.clear()
                with self.subTest(operation=operation, endpoint=url), \
                        patch('rest_framework.throttling.SimpleRateThrottle.cache.'+operation,
                              side_effect=RedisConnectionError('private redis host and credentials')):
                    response = self.client.post(url, data, format='json')
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.data['detail'].code, 'rate_limit_unavailable')
                self.assertNotIn('private', str(response.data))
        self.assertEqual(OutstandingToken.objects.count(), 0)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password('StrongAuditPass!92'))
        self.assertFalse(PasswordResetChallenge.objects.filter(user=self.admin).exists())

    def test_authenticated_writes_and_expensive_reads_do_not_run_during_outage(self):
        self.client.force_authenticate(self.admin)
        with patch('rest_framework.throttling.SimpleRateThrottle.cache.get', side_effect=ConnectionError('cache down')), \
                patch('sales.views.checkout_views.SalesService.checkout', side_effect=AssertionError('must not mutate')), \
                patch('forecasting.views.enqueue', side_effect=AssertionError('must not enqueue')), \
                patch('reporting.views.refresh_reports', side_effect=AssertionError('must not rebuild')):
            for url in ('/sales/checkout/', '/api/forecasting/refresh/', '/api/reports/refresh/'):
                self.assertEqual(self.client.post(url, {}, format='json').status_code, 503)
            for url in ('/api/forecasting/chart/', '/api/forecasting/evidence/', '/api/reports/preview/?type=daily_sales'):
                self.assertEqual(self.client.get(url).status_code, 503)
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(ForecastRefreshJob.objects.count(), 0)

    def test_permission_denials_still_precede_rate_limit_storage(self):
        with patch('rest_framework.throttling.SimpleRateThrottle.cache.get', side_effect=RedisConnectionError('down')):
            self.assertEqual(self.client.post('/api/forecasting/refresh/', {}, format='json').status_code, 401)
            self.admin.role = 'staff'
            self.admin.save(update_fields=['role'])
            self.client.force_authenticate(self.admin)
            self.assertEqual(self.client.post('/api/forecasting/refresh/', {}, format='json').status_code, 403)

    def test_recovery_restores_normal_requests_and_existing_limits(self):
        data = {'username': self.admin.username, 'password': 'StrongAuditPass!92'}
        with patch('rest_framework.throttling.SimpleRateThrottle.cache.get', side_effect=RedisConnectionError('down')):
            self.assertEqual(self.client.post('/accounts/login/', data, format='json').status_code, 503)
        self.assertEqual(self.client.post('/accounts/login/', data, format='json').status_code, 200)
        self.client.force_authenticate(self.admin)
        with patch('forecasting.refresh_jobs.launch'):
            self.assertEqual(self.client.post('/api/forecasting/refresh/', {}, format='json').status_code, 202)
            self.assertEqual(self.client.post('/api/forecasting/refresh/', {}, format='json').status_code, 429)

    def test_refresh_rejects_non_object_json_without_enqueuing_work(self):
        self.client.force_authenticate(self.admin)
        with patch('forecasting.views.enqueue', side_effect=AssertionError('invalid body must not enqueue')):
            for body in (None, [], '', False, 0):
                cache.clear()
                response = self.client.generic('POST', '/api/forecasting/refresh/', json.dumps(body),
                                               content_type='application/json')
                self.assertEqual(response.status_code, 400)
        self.assertFalse(ForecastRefreshJob.objects.exists())

    def test_database_connection_failures_have_safe_api_responses_without_automatic_retries(self):
        with self.assertLogs('config.exceptions', level='ERROR'), \
                patch('accounts.views.login_views.Users.objects.filter', side_effect=OperationalError('private database password')) as lookup:
            response = self.client.post('/accounts/login/', {'username': self.admin.username,
                'password': 'StrongAuditPass!92'}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', str(response.data))
        lookup.assert_called_once()
        self.assertFalse(OutstandingToken.objects.exists())
        self.client.force_authenticate(self.admin)
        with self.assertLogs('config.exceptions', level='ERROR'), \
                patch('forecasting.views.enqueue', side_effect=OperationalError('private database address')) as enqueue:
            response = self.client.post('/api/forecasting/refresh/', {}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['detail'].code, 'database_unavailable')
        enqueue.assert_called_once()
        with self.assertLogs('config.exceptions', level='ERROR'), \
                patch('forecasting.views.chart_report', side_effect=InterfaceError('private database address')):
            response = self.client.get('/api/forecasting/chart/?period=weekly')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', str(response.data))
        self.assertFalse(ForecastRefreshJob.objects.exists())

    def test_unrelated_programming_errors_are_not_disguised_as_connection_failures(self):
        from config.exceptions import api_exception_handler
        self.assertIsNone(api_exception_handler(ValueError('programming bug'), {}))


class CustomerVisibilityAuditTests(APITestCase):
    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from inventory.models import Category, Product, ProductBatch
        from sales.models import Customer, TransactionItem
        cache.clear()
        users = get_user_model()
        self.admin = users.objects.create_user(username='visibility-audit-admin', email='visibility-admin@example.test', role='admin')
        self.staff = users.objects.create_user(username='visibility-audit-staff', email='visibility-staff@example.test', role='staff')
        self.customer = Customer.objects.create(name='Visible and hidden sales', created_by=self.admin)
        self.hidden_customer = Customer.objects.create(name='Hidden sales only', created_by=self.admin)
        batches = []
        for name, visible, active in [('visible-audit', True, True), ('hidden-audit', False, True), ('inactive-audit', True, False)]:
            category = Category.objects.create(name=name, is_visible_to_staff=visible, is_active=active)
            product = Product.objects.create(name=name, category=category, unit='piece', shelf_life=7)
            batches.append(ProductBatch.objects.create(product=product, batch_number=name, initial_quantity=10,
                remaining_quantity=10, expiration_date=timezone.localdate()+timedelta(days=7)))
        self.hidden_category = batches[1].product.category
        self.visible_at = timezone.now()-timedelta(days=2)
        for customer, selected, voided, stamp in [
            (self.customer, [batches[0]], False, self.visible_at),
            (self.customer, [batches[1]], False, timezone.now()-timedelta(days=1)),
            (self.customer, [batches[2]], False, timezone.now()-timedelta(days=1)),
            (self.customer, batches[:2], False, timezone.now()-timedelta(days=1)),
            (self.customer, [batches[0]], True, timezone.now()),
            (self.hidden_customer, [batches[1]], False, timezone.now())]:
            sale = Transaction.objects.create(handled_by=self.admin, customer=customer,
                total_amount=50*len(selected), is_voided=voided)
            Transaction.objects.filter(pk=sale.pk).update(created_at=stamp)
            for batch in selected:
                TransactionItem.objects.create(transaction=sale, product_batch=batch, quantity=1, unit_price=50)

    def test_customer_list_and_detail_summaries_match_staff_visible_history_even_with_warm_admin_cache(self):
        from django.utils.dateparse import parse_datetime
        self.client.force_authenticate(self.admin)
        admin_rows = {r['id']: r for r in self.client.get('/sales/customers/').data}
        self.assertEqual(admin_rows[self.customer.pk]['transaction_count'], 4)
        self.client.force_authenticate(self.staff)
        for url in ('/sales/customers/', f'/sales/customers/{self.customer.pk}/'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            row = next(r for r in response.data if r['id']==self.customer.pk) if isinstance(response.data, list) else response.data
            self.assertEqual(row['transaction_count'], 1)
            self.assertEqual(parse_datetime(row['last_sale']), self.visible_at)
        hidden = self.client.get(f'/sales/customers/{self.hidden_customer.pk}/').data
        self.assertEqual(hidden['transaction_count'], 0)
        self.assertIsNone(hidden['last_sale'])
        self.assertEqual(self.client.get(f'/sales/customers/{self.customer.pk}/transactions/').data['count'], 1)

    def test_visibility_change_invalidates_cached_customer_summary(self):
        self.client.force_authenticate(self.staff)
        before = {r['id']: r for r in self.client.get('/sales/customers/').data}
        self.assertEqual(before[self.customer.pk]['transaction_count'], 1)
        with self.capture_commit_callbacks(execute=True):
            self.hidden_category.is_visible_to_staff = True
            self.hidden_category.save(update_fields=['is_visible_to_staff'])
        after = {r['id']: r for r in self.client.get('/sales/customers/').data}
        self.assertEqual(after[self.customer.pk]['transaction_count'], 3)
        self.assertEqual(after[self.hidden_customer.pk]['transaction_count'], 1)


class HTTPParserSecurityTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_oversized_accept_token_is_rejected_before_login(self):
        response = self.client.post('/accounts/login/', {'username': 'unused', 'password': 'unused'},
            format='json', HTTP_ACCEPT='application/json; x="'+';'*50000+'"')
        self.assertEqual(response.status_code, 406)
        self.assertFalse(OutstandingToken.objects.exists())

    def test_non_text_body_charsets_return_validation_errors(self):
        for charset in ('base64_codec', 'hex_codec', 'bz2_codec'):
            cache.clear()
            response = self.client.generic('POST', '/accounts/login/', b'{}',
                content_type='application/json; charset='+charset)
            self.assertEqual(response.status_code, 400)
        self.assertFalse(OutstandingToken.objects.exists())


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PasswordResetIdentityRaceTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username='reset-before-rename',
            email='reset-race@example.test', password='StrongAuditPass!92')

    def test_rename_between_lookup_and_lock_does_not_issue_code_or_start_cooldown(self):
        from django.core import mail
        from accounts.services import password_reset_service as service

        def rename_after_lookup(*args):
            get_user_model().objects.filter(pk=self.user.pk).update(username='reset-after-rename')
            return self.user  # Snapshot read before the concurrent profile update.

        with patch.object(service, '_matching_active_user', side_effect=rename_after_lookup):
            response = self.client.post('/accounts/forgot-password/', {
                'username': self.user.username, 'email': self.user.email}, format='json')
        self.assertEqual(response.status_code, 200)  # Keep the non-enumerating response.
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(PasswordResetChallenge.objects.filter(user=self.user).exists())
        self.user.refresh_from_db()
        with patch.object(service.secrets, 'randbelow', return_value=123456):
            service.request_password_reset(self.user.username, self.user.email)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(service.reset_password_with_otp(self.user.username, self.user.email,
                                                       '123456', 'ChangedAuditPass!92'))

    def test_old_username_request_cannot_consume_new_identity_code_after_lookup_race(self):
        from accounts.services import password_reset_service as service

        old_username = self.user.username
        get_user_model().objects.filter(pk=self.user.pk).update(username='reset-after-rename')
        with patch.object(service.secrets, 'randbelow', return_value=123456):
            service.request_password_reset('reset-after-rename', self.user.email)
        with patch.object(service, '_matching_active_user', return_value=self.user):
            self.assertFalse(service.reset_password_with_otp(old_username, self.user.email,
                '123456', 'ChangedAuditPass!92'))
        self.assertTrue(PasswordResetChallenge.objects.filter(user=self.user, failed_attempts=0).exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('StrongAuditPass!92'))
        self.assertTrue(service.reset_password_with_otp(self.user.username, self.user.email,
            '123456', 'ChangedAuditPass!92'))


class DatabaseTimeoutConfigurationTests(SimpleTestCase):
    def test_timeout_is_forwarded_to_postgresql_without_opening_a_connection(self):
        import os
        import runpy
        from pathlib import Path
        from django.db.backends.postgresql.base import DatabaseWrapper

        for value in ('5', '9'):
            with self.subTest(timeout=value), patch.dict(os.environ, {
                    'DJANGO_ENV': 'development', 'DB_CONNECT_TIMEOUT': value}):
                configured = runpy.run_path(str(Path(__file__).with_name('settings.py')))
                wrapper = DatabaseWrapper(configured['DATABASES']['default'])
                self.assertEqual(wrapper.get_connection_params()['connect_timeout'], int(value))
                self.assertIsNone(wrapper.connection)

    def test_zero_negative_and_invalid_timeouts_fail_at_startup(self):
        import os
        import runpy
        from pathlib import Path
        from django.core.exceptions import ImproperlyConfigured

        for value in ('0', '-1', '1', 'invalid', '2.5', ''):
            with self.subTest(timeout=value), patch.dict(os.environ, {
                    'DJANGO_ENV': 'development', 'DB_CONNECT_TIMEOUT': value}):
                with self.assertRaisesMessage(ImproperlyConfigured, 'DB_CONNECT_TIMEOUT'):
                    runpy.run_path(str(Path(__file__).with_name('settings.py')))


class RedisTimeoutConfigurationTests(SimpleTestCase):
    def test_unresponsive_redis_times_out_without_retries_before_the_browser_deadline(self):
        import os
        from pathlib import Path
        import runpy
        import socket
        from threading import Event, Thread
        import time
        from django.core.cache.backends.redis import RedisCache
        from redis.exceptions import TimeoutError as RedisTimeoutError

        stop = Event()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            listener.settimeout(5)
            url = f'redis://127.0.0.1:{listener.getsockname()[1]}/0'

            def blackhole():
                try:
                    peer, _ = listener.accept()
                    with peer:
                        # Accept TCP but never answer a Redis command. A dead
                        # broker differs from an immediately refused socket.
                        stop.wait(4)
                except OSError:
                    pass

            worker = Thread(target=blackhole, daemon=True)
            worker.start()
            with patch.dict(os.environ, {'DJANGO_ENV': 'development', 'REDIS_URL': url}):
                configured = runpy.run_path(str(Path(__file__).with_name('settings.py')))
            backend = configured['CACHES']['default']
            client = RedisCache(backend['LOCATION'], backend)
            try:
                pool = client._cache.get_client().connection_pool
                self.assertEqual(pool.connection_kwargs['socket_connect_timeout'], 1)
                started = time.monotonic()
                with self.assertRaises(RedisTimeoutError):
                    client.get('qa-stalled-broker')
                self.assertLess(time.monotonic() - started, 2.5)
            finally:
                stop.set()
                client.close()
                worker.join(timeout=1)


class BatchBusinessDateAuditTests(APITestCase):
    def setUp(self):
        from inventory.models import Category, Product
        self.product = Product.objects.create(name='Month-boundary milk',
            category=Category.objects.create(name='Month-boundary dairy'), unit='piece', shelf_life=7)

    def test_manila_month_and_year_boundaries_use_matching_local_counters_and_prefixes(self):
        from datetime import datetime, timezone as datetime_timezone
        from django.utils import timezone
        from inventory.serializers import ProdBatchSerializer
        from inventory.models import BatchSequence
        cases = [(datetime(2026,10,31,16,5,tzinfo=datetime_timezone.utc), '2611', '2026-11-08', 2026, 11),
                 (datetime(2026,12,31,16,5,tzinfo=datetime_timezone.utc), '2701', '2027-01-08', 2027, 1)]
        for stamp, prefix, expiry, year, month in cases:
            with timezone.override('Asia/Manila'), patch('django.utils.timezone.now', return_value=stamp):
                for (serializer_type, field, obj, kind) in [(ProdBatchSerializer, 'product_id', self.product, 'PRD')]:
                    serializer = serializer_type(data={field:obj.pk,'quantity':'1.00','expiration_date':expiry})
                    self.assertTrue(serializer.is_valid(),serializer.errors)
                    batch = serializer.save()
                    self.assertEqual(batch.batch_number, f'{kind}-{prefix}-001')
                    self.assertEqual(BatchSequence.objects.get(prefix=kind,year=year,month=month).last_seq,1)

    def test_creation_keeps_one_counter_date_even_if_clock_crosses_midnight_after_allocation(self):
        from datetime import date
        from inventory.serializers import ProdBatchSerializer
        from inventory.services.batch_sequence_service import next_sequence
        from inventory.models import BatchSequence
        for (serializer_type, field, obj, kind, module) in [(ProdBatchSerializer, 'product_id', self.product, 'PRD', 'inventory.serializers.productbatch_serializer')]:
            serializer = serializer_type(data={field:obj.pk,'quantity':'1.00','expiration_date':'2027-01-08'})
            self.assertTrue(serializer.is_valid(),serializer.errors)
            with patch(module+'.timezone.localdate', side_effect=[date(2026,12,31), date(2027,1,1)]) as localdate, \
                    patch(module+'.next_sequence', wraps=next_sequence) as allocate:
                batch = serializer.save()
                localdate.assert_called_once_with()
                allocate.assert_called_once_with(kind,business_date=date(2026,12,31))
            self.assertEqual(batch.batch_number,f'{kind}-2612-001')
            self.assertEqual(BatchSequence.objects.get(prefix=kind,year=2026,month=12).last_seq,1)
