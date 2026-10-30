"""Production probes must work with strict HTTPS, and runtime roles stay isolated."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.middleware.security import SecurityMiddleware
from django.test import RequestFactory, SimpleTestCase, override_settings

from config.health import healthcheck_headers


ROOT = Path(__file__).resolve().parents[1]


@override_settings(ALLOWED_HOSTS=['app.example.test'], HEALTHCHECK_HOST='app.example.test',
    SECURE_SSL_REDIRECT=True, SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'))
class ProductionHealthTests(SimpleTestCase):
    def test_probe_headers_pass_real_security_middleware_without_a_redirect(self):
        headers = healthcheck_headers()
        request = RequestFactory().get('/events/', **{
            'HTTP_' + name.upper().replace('-', '_'): value for name, value in headers.items()})
        response = SecurityMiddleware(lambda request: HttpResponse(status=401))(request)
        self.assertEqual(response.status_code, 401)

    @override_settings(HEALTHCHECK_HOST='')
    def test_probe_defaults_to_a_configured_allowed_host(self):
        self.assertEqual(healthcheck_headers()['Host'], 'app.example.test')

    @override_settings(HEALTHCHECK_HOST='untrusted.example.test')
    def test_unknown_health_host_is_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            healthcheck_headers()

    @override_settings(SECURE_PROXY_SSL_HEADER=None)
    def test_direct_development_probe_does_not_claim_proxy_https(self):
        self.assertEqual(healthcheck_headers(), {'Host': 'app.example.test'})

    def test_event_listener_probe_uses_the_same_host_and_https_rules(self):
        check = runpy.run_path(str(ROOT/'docker/events_healthcheck.py'))['check']
        with patch('http.client.HTTPConnection') as http:
            http.return_value.getresponse.return_value.status = 401
            check()
            http.return_value.request.assert_called_once_with('GET', '/events/', headers=healthcheck_headers())
            http.return_value.close.assert_called_once()


class ProvisioningValidationTests(SimpleTestCase):
    def setUp(self):
        self.provision = runpy.run_path(str(ROOT/'docker/provision_database.py'))['provision']
        self.environment = {'DB_NAME': 'application', 'POSTGRES_ADMIN_USER': 'owner',
            'POSTGRES_ADMIN_PASSWORD': 'administrator-password-for-tests',
            'MIGRATION_DB_USER': 'migrator', 'MIGRATION_DB_PASSWORD': 'migration-password-for-tests',
            'DB_USER': 'runtime', 'DB_PASSWORD': 'runtime-password-for-tests'}

    def test_missing_settings_duplicate_roles_and_password_reuse_fail_before_connecting(self):
        cases = [{'DB_NAME': ''}, {'DB_USER': 'owner'}, {'DB_USER': 'migrator'},
                 {'DB_PASSWORD': 'administrator-password-for-tests'}, {'DB_PASSWORD': 'short'},
                 {'DB_USER': 'x' * 64}]
        with patch('psycopg2.connect') as connect:
            for changes in cases:
                with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                    self.provision({**self.environment, **changes})
            connect.assert_not_called()


class ProductionPreflightTests(SimpleTestCase):
    def setUp(self):
        self.check = runpy.run_path(str(ROOT/'docker/production_preflight.py'))['check']

    def test_hsts_scope_advisories_do_not_prevent_migration_verification(self):
        issues = [checks.Warning('Subdomain decision', id='security.W005'),
                  checks.Warning('Preload decision', id='security.W021')]
        with patch('django.core.checks.run_checks', return_value=issues) as run_checks, \
             patch('django.core.management.call_command') as command, \
             redirect_stderr(StringIO()) as output, redirect_stdout(StringIO()):
            self.check()
        run_checks.assert_called_once_with(include_deployment_checks=True, databases=['default'])
        command.assert_called_once_with('migrate', check=True)
        self.assertIn('security.W005', output.getvalue())
        self.assertIn('security.W021', output.getvalue())

    def test_other_warnings_and_errors_block_startup(self):
        for issue in (checks.Warning('Unexpected warning', id='security.W999'),
                      checks.Error('Database privileges', id='config.E002')):
            with self.subTest(issue=issue.id), \
                 patch('django.core.checks.run_checks', return_value=[issue]), \
                 patch('django.core.management.call_command') as command, \
                 redirect_stderr(StringIO()), self.assertRaises(RuntimeError):
                self.check()
            command.assert_not_called()


@unittest.skipUnless(shutil.which('docker'), 'Requires the Docker Compose CLI')
class ProductionComposeTests(SimpleTestCase):
    def config(self):
        environment = dict(os.environ,
            DJANGO_SECRET_KEY='test-only-random-looking-secret-value-which-is-long-enough-1234567890',
            DJANGO_ALLOWED_HOSTS='app.example.test', DJANGO_HEALTHCHECK_HOST='app.example.test',
            DB_NAME='application', DB_USER='runtime', DB_PASSWORD='runtime-password-for-tests',
            POSTGRES_ADMIN_USER='owner', POSTGRES_ADMIN_PASSWORD='administrator-password-for-tests',
            MIGRATION_DB_USER='migrator', MIGRATION_DB_PASSWORD='migration-password-for-tests',
            CORS_ALLOWED_ORIGINS='https://app.example.test', CSRF_TRUSTED_ORIGINS='https://app.example.test',
            SMTP_HOST='smtp.example.test', SMTP_USER='sender@example.test', SMTP_PASSWORD='smtp-password-for-tests',
            DEFAULT_FROM_EMAIL='sender@example.test')
        process = subprocess.run(['docker', 'compose', '--env-file', '/dev/null', '-f',
            str(ROOT/'compose.production.yaml'), '--profile', 'frontend', 'config', '--format', 'json'],
            env=environment, capture_output=True, text=True, check=True)
        return json.loads(process.stdout)['services']

    def test_application_has_production_email_settings_but_no_operator_passwords(self):
        services = self.config()
        for name in ('api', 'events', 'forecast-worker', 'preflight'):
            environment = services[name]['environment']
            self.assertEqual(environment['DJANGO_ENV'], 'production')
            self.assertEqual(environment['DB_USER'], 'runtime')
            self.assertEqual(environment['SMTP_HOST'], 'smtp.example.test')
            self.assertEqual(environment['DJANGO_BEHIND_HTTPS_PROXY'], 'true')
            for secret in ('POSTGRES_ADMIN_PASSWORD', 'MIGRATION_DB_PASSWORD'):
                self.assertNotIn(secret, environment)
        self.assertEqual(services['migrate']['environment']['DB_USER'], 'migrator')
        self.assertEqual(services['db']['environment']['POSTGRES_USER'], 'owner')

    def test_preflight_gates_startup_and_services_do_not_expose_database_or_app_ports(self):
        services = self.config()
        for name in ('api', 'events', 'forecast-worker'):
            self.assertEqual(services[name]['depends_on']['preflight']['condition'], 'service_completed_successfully')
        for name in ('db', 'redis', 'api', 'events'):
            self.assertFalse(services[name].get('ports'))
        for name in ('gateway', 'frontend'):
            self.assertTrue(all(port['host_ip'] == '127.0.0.1' for port in services[name]['ports']))
