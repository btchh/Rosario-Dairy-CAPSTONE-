"""Ordinary report use must not consume the forecast/rebuild allowance."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
import json
from threading import Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connections
from django.test import override_settings
from django.utils import timezone

from config.test_support import APIClient, APITransactionTestCase


CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                     'LOCATION': 'report-availability-regressions'}}


@override_settings(CACHES=CACHE)
class ReportReadAllowanceTests(APITransactionTestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username='report-availability', email='report-availability@example.test', role='admin')
        self.client.force_authenticate(self.user)

    def preview(self):
        return self.client.get('/api/reports/preview/', {'type': 'daily_sales'})

    def pdf(self):
        response = self.client.get('/api/reports/export-pdf/', {'type': 'daily_sales'})
        if response.status_code == 200:
            self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))
            response.close()
        return response

    def test_exhausted_forecast_allowance_does_not_block_preview_or_export(self):
        with patch('forecasting.views.models_report', return_value={'status': 'no_history'}):
            for _ in range(30):
                self.assertEqual(self.client.get('/api/forecasting/models/').status_code, 200)
            self.assertEqual(self.client.get('/api/forecasting/models/').status_code, 429)
        self.assertEqual(self.preview().status_code, 200)
        with patch('reporting.views.generate_pdf', return_value=BytesIO(b'%PDF test')):
            self.assertEqual(self.pdf().status_code, 200)

    def test_more_than_thirty_previews_do_not_consume_the_forecast_allowance(self):
        # Repeated default cards previously exhausted the shared 30/minute budget.
        for _ in range(36):
            response = self.preview()
            self.assertEqual(response.status_code, 200, response.data)
        with patch('forecasting.views.models_report', return_value={'status': 'no_history'}):
            self.assertEqual(self.client.get('/api/forecasting/models/').status_code, 200)

    def test_export_allowance_still_limits_work_without_blocking_previews(self):
        with patch('accounts.throttles.ReportExportRateThrottle.rate', '2/minute', create=True), \
                patch('reporting.views.generate_pdf', side_effect=lambda *_: BytesIO(b'%PDF test')) as build:
            self.assertEqual(self.pdf().status_code, 200)
            self.assertEqual(self.pdf().status_code, 200)
            response = self.pdf()
            self.assertEqual(response.status_code, 429)
            self.assertGreater(int(response['Retry-After']), 0)
            self.assertEqual(build.call_count, 2)
        self.assertEqual(self.preview().status_code, 200)

    def test_preview_allowance_is_independent_and_still_bounded(self):
        with patch('accounts.throttles.ReportPreviewRateThrottle.rate', '2/minute', create=True):
            self.assertEqual(self.preview().status_code, 200)
            self.assertEqual(self.preview().status_code, 200)
            self.assertEqual(self.preview().status_code, 429)
        with patch('reporting.views.generate_pdf', return_value=BytesIO(b'%PDF test')):
            self.assertEqual(self.pdf().status_code, 200)

    def test_invalid_refresh_does_not_consume_the_rebuild_allowance(self):
        with patch('reporting.views.refresh_reports', return_value=(timezone.now(), {})) as build:
            self.assertEqual(self.client.get('/api/reports/refresh/').status_code, 405)
            for body in (None, [], True, 'bad', {'unknown': 'field'}):
                response = self.client.generic('POST', '/api/reports/refresh/', json.dumps(body),
                                               content_type='application/json')
                self.assertEqual(response.status_code, 400, response.data)
            build.assert_not_called()
            response = self.client.post('/api/reports/refresh/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['refresh_status'], 'refreshed')
        self.assertEqual(build.call_count, 2)

    def test_coalesced_refresh_preserves_the_real_generation_timestamp(self):
        generated = timezone.now() - timedelta(minutes=5)
        with patch('reporting.views.refresh_reports', return_value=(generated, {})) as build:
            first = self.client.post('/api/reports/refresh/', {}, format='json')
            second = self.client.post('/api/reports/refresh/', {}, format='json')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data['refresh_status'], 'coalesced')
        self.assertEqual(second.data['generated_at'], generated.isoformat())
        self.assertEqual(second.data['report_types'], [])
        self.assertEqual(build.call_count, 2)


@override_settings(CACHES=CACHE)
class ActiveReportRefreshTests(APITransactionTestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(username='active-report-admin', email='active-report-admin@example.test', role='admin')
        self.staff = get_user_model().objects.create_user(username='active-report-staff', email='active-report-staff@example.test', role='staff')
        self.client.force_authenticate(self.admin)

    def test_running_refresh_accepts_duplicate_and_keeps_previews_available(self):
        entered, release = Event(), Event()

        def build(**kwargs):
            entered.set()
            if not release.wait(timeout=15):
                raise RuntimeError('Refresh test was not released.')
            return timezone.now(), {}

        def refresh():
            try:
                client = APIClient()
                client.force_authenticate(self.admin)
                return client.post('/api/reports/refresh/', {}, format='json')
            finally:
                connections.close_all()

        with patch('reporting.views.refresh_reports', side_effect=build) as rebuild:
            with ThreadPoolExecutor(max_workers=1) as pool:
                active = pool.submit(refresh)
                try:
                    self.assertTrue(entered.wait(timeout=10))
                    self.client.force_authenticate(self.staff)
                    duplicate = self.client.post('/api/reports/refresh/', {}, format='json')
                    self.assertEqual(duplicate.status_code, 200, duplicate.data)
                    self.assertEqual(duplicate.data['refresh_status'], 'coalesced')
                    self.assertIsNone(duplicate.data['generated_at'])
                    preview = self.client.get('/api/reports/preview/', {'type': 'daily_sales'})
                    self.assertEqual(preview.status_code, 200, preview.data)
                    self.assertEqual(rebuild.call_count, 1)
                finally:
                    release.set()
                first = active.result(timeout=15)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data['refresh_status'], 'refreshed')
        self.assertEqual(rebuild.call_count, 2)
