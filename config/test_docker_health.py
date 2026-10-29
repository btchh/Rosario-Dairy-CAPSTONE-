"""Container readiness must fail safely when any required service is unavailable."""
from contextlib import ExitStack, redirect_stderr
from io import StringIO
from pathlib import Path
import runpy
from unittest.mock import patch

from django.db import OperationalError, connections
from django.test import SimpleTestCase


HEALTHCHECK = Path(__file__).resolve().parent.parent/'docker'/'healthcheck.py'


class DockerHealthTests(SimpleTestCase):
    def setUp(self):
        self.check = runpy.run_path(str(HEALTHCHECK))['check']
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        database = connections['default']
        self.cursor = self.stack.enter_context(patch.object(database, 'cursor'))
        self.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
        self.close_database = self.stack.enter_context(patch.object(database, 'close'))
        self.stack.enter_context(patch('django.core.cache.cache.set'))
        self.cache_read = self.stack.enter_context(patch('django.core.cache.cache.get', side_effect=lambda key: key))
        self.cache_delete = self.stack.enter_context(patch('django.core.cache.cache.delete'))
        self.http = self.stack.enter_context(patch('http.client.HTTPConnection'))
        self.http.return_value.getresponse.return_value.status = 401

    def test_database_cache_and_unauthenticated_user_route_pass_readiness(self):
        self.check()
        self.http.return_value.request.assert_called_once_with(
            'GET', '/accounts/user/', headers={'Host': 'localhost'})
        self.cache_delete.assert_called_once()
        self.close_database.assert_called_once()
        self.http.return_value.close.assert_called_once()

    def test_database_outage_fails_before_contacting_the_http_process(self):
        self.cursor.side_effect = OperationalError('database unavailable')
        with self.assertRaises(OperationalError):
            self.check()
        self.http.assert_not_called()

    def test_cache_that_cannot_store_and_retrieve_the_probe_fails_readiness(self):
        self.cache_read.side_effect = None
        self.cache_read.return_value = None
        with self.assertRaises(RuntimeError):
            self.check()
        self.http.assert_not_called()
        self.cache_delete.assert_called_once()
        self.close_database.assert_called_once()

    def test_unexpected_http_response_does_not_report_a_healthy_api(self):
        for status in (200, 301, 403, 404, 500, 503):
            with self.subTest(status=status):
                self.http.return_value.getresponse.return_value.status = status
                with self.assertRaises(RuntimeError):
                    self.check()
        self.assertEqual(self.http.return_value.close.call_count, 6)

    def test_unreachable_http_process_fails_and_closes_the_connection(self):
        self.http.return_value.request.side_effect = TimeoutError
        with self.assertRaises(TimeoutError):
            self.check()
        self.http.return_value.close.assert_called_once()

    def test_command_reports_failure_without_disclosing_connection_details(self):
        self.cursor.side_effect = OperationalError('private password and connection information')
        error = StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit) as stopped:
            runpy.run_path(str(HEALTHCHECK), run_name='__main__')
        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(error.getvalue(), 'Backend health check failed.\n')
