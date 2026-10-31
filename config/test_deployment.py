"""Production configuration must fail before serving with development settings."""
import os
from pathlib import Path
import runpy
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError
from django.test import SimpleTestCase, override_settings

from config.checks import check_application_database_role


class ProductionSettingsTests(SimpleTestCase):
    def configure(self, changes=None, omit=()):
        environment = {
            'DJANGO_ENV': 'production',
            'DJANGO_SECRET_KEY': 'test-only-production-configuration-secret-' * 2,
            'DJANGO_ALLOWED_HOSTS': 'api.example.test',
            'DB_NAME': 'production_test', 'DB_USER': 'dairy_app',
            'DB_PASSWORD': 'test-only-database-password', 'DB_HOST': 'database.example.test',
            'REDIS_URL': 'redis://cache.example.test:6379/0',
            'SMTP_HOST': 'smtp.example.test', 'SMTP_USER': 'sender@example.test',
            'SMTP_PASSWORD': 'test-only-smtp-password', 'DEFAULT_FROM_EMAIL': 'sender@example.test',
        }
        environment.update(changes or {})
        for name in omit:
            environment.pop(name, None)
        with patch.dict(os.environ, environment, clear=True), patch('dotenv.load_dotenv'):
            return runpy.run_path(str(Path(__file__).with_name('settings.py')))

    def test_secure_defaults_and_explicit_proxy_trust(self):
        configured = self.configure()
        self.assertFalse(configured['DEBUG'])
        for setting in ('SECURE_SSL_REDIRECT', 'SESSION_COOKIE_SECURE', 'CSRF_COOKIE_SECURE'):
            self.assertTrue(configured[setting])
        self.assertGreater(configured['SECURE_HSTS_SECONDS'], 0)
        self.assertNotIn('SECURE_PROXY_SSL_HEADER', configured)
        self.assertEqual(self.configure({'DJANGO_BEHIND_HTTPS_PROXY': 'true'})['SECURE_PROXY_SSL_HEADER'],
                         ('HTTP_X_FORWARDED_PROTO', 'https'))
        self.assertEqual(configured['CACHES']['default']['BACKEND'],
                         'django.core.cache.backends.redis.RedisCache')

    def test_idle_policy_defaults_overrides_and_invalid_values(self):
        configured = self.configure()
        self.assertEqual(configured['AUTH_IDLE_TIMEOUT_SECONDS'], 900)
        self.assertEqual(configured['AUTH_IDLE_WARNING_SECONDS'], 60)
        configured = self.configure({'AUTH_IDLE_TIMEOUT_SECONDS': '1800', 'AUTH_IDLE_WARNING_SECONDS': '120'})
        self.assertEqual(configured['AUTH_IDLE_TIMEOUT_SECONDS'], 1800)
        self.assertEqual(configured['AUTH_IDLE_WARNING_SECONDS'], 120)
        for changes in (
            {'AUTH_IDLE_TIMEOUT_SECONDS': 'zero'}, {'AUTH_IDLE_TIMEOUT_SECONDS': '0'},
            {'AUTH_IDLE_TIMEOUT_SECONDS': '-1'}, {'AUTH_IDLE_WARNING_SECONDS': '-1'},
            {'AUTH_IDLE_WARNING_SECONDS': '900'}, {'AUTH_IDLE_WARNING_SECONDS': '901'},
            {'AUTH_IDLE_WARNING_SECONDS': '1.5'},
        ):
            with self.subTest(changes=changes), self.assertRaises(ImproperlyConfigured):
                self.configure(changes)

    def test_local_blank_compose_smtp_uses_console_with_a_default_sender(self):
        configured = self.configure({'DJANGO_ENV': 'development', 'SMTP_HOST': '',
            'SMTP_USER': '', 'SMTP_PASSWORD': '', 'DEFAULT_FROM_EMAIL': ''})
        self.assertEqual(configured['EMAIL_BACKEND'], 'django.core.mail.backends.console.EmailBackend')
        self.assertEqual(configured['DEFAULT_FROM_EMAIL'], 'Rosario Dairy <noreply@localhost>')

    def test_complete_local_smtp_overrides_console_and_incomplete_settings_fail(self):
        configured = self.configure({'DJANGO_ENV': 'development',
            'DJANGO_EMAIL_BACKEND': 'django.core.mail.backends.console.EmailBackend'})
        self.assertEqual(configured['EMAIL_BACKEND'], 'django.core.mail.backends.smtp.EmailBackend')
        self.assertEqual(configured['EMAIL_HOST_USER'], 'sender@example.test')
        for missing in ('SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD', 'DEFAULT_FROM_EMAIL'):
            with self.subTest(missing=missing), self.assertRaises(ImproperlyConfigured):
                self.configure({'DJANGO_ENV': 'development', missing: ''})

    def test_database_configuration_cannot_fall_back_to_development(self):
        for setting in ('DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_HOST'):
            for value in (None, '', '   '):
                with self.subTest(setting=setting, value=value):
                    with self.assertRaisesMessage(ImproperlyConfigured, setting):
                        if value is None:
                            self.configure(omit=(setting,))
                        else:
                            self.configure({setting: value})
        for user in ('postgres', 'root', 'Postgres'):
            with self.subTest(user=user), self.assertRaisesMessage(ImproperlyConfigured, 'DB_USER'):
                self.configure({'DB_USER': user})
        with self.assertRaisesMessage(ImproperlyConfigured, 'DB_PASSWORD'):
            self.configure({'DB_PASSWORD': 'root'})

    def test_production_rejects_unsafe_security_overrides(self):
        cases = {
            'DJANGO_DEBUG': 'true', 'DJANGO_ALLOWED_HOSTS': '*',
            'DJANGO_SECURE_SSL_REDIRECT': 'false',
            'DJANGO_SESSION_COOKIE_SECURE': 'false', 'DJANGO_CSRF_COOKIE_SECURE': 'false',
            'DJANGO_SECURE_HSTS_SECONDS': '0',
        }
        for setting, value in cases.items():
            with self.subTest(setting=setting), self.assertRaisesMessage(ImproperlyConfigured, setting):
                self.configure({setting: value})

    def test_required_production_services_cannot_silently_use_development_fallbacks(self):
        for setting in ('DJANGO_SECRET_KEY', 'DJANGO_ALLOWED_HOSTS', 'REDIS_URL',
                        'SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD', 'DEFAULT_FROM_EMAIL'):
            with self.subTest(setting=setting), self.assertRaises(ImproperlyConfigured):
                self.configure(omit=(setting,))

    def test_configured_browser_origins_require_https_and_explicit_hosts(self):
        for setting in ('CORS_ALLOWED_ORIGINS', 'CSRF_TRUSTED_ORIGINS'):
            for origin in ('http://app.example.test', 'https://*.example.test',
                           'https://user:password@app.example.test', 'https://app.example.test/path'):
                with self.subTest(setting=setting, origin=origin):
                    with self.assertRaisesMessage(ImproperlyConfigured, setting):
                        self.configure({setting: origin})
            self.assertEqual(self.configure({setting: 'https://app.example.test:8443'})[setting],
                             ['https://app.example.test:8443'])


@override_settings(IS_PRODUCTION=True)
class ProductionDatabaseRoleTests(SimpleTestCase):
    def check_role(self, privileges):
        with patch('config.checks.connections') as databases:
            database = databases['default']
            database.vendor = 'postgresql'
            cursor = database.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = privileges
            result = check_application_database_role(None, databases=['default'])
            cursor.execute.assert_called_once()
            return result

    def test_read_only_role_check_accepts_unprivileged_role(self):
        self.assertEqual(self.check_role((False,) * 5), [])

    def test_each_elevated_privilege_and_unknown_role_block_deployment(self):
        for index in range(5):
            privileges = [False] * 5
            privileges[index] = True
            with self.subTest(privilege=index):
                self.assertEqual(self.check_role(privileges)[0].id, 'config.E002')
        self.assertEqual(self.check_role(None)[0].id, 'config.E002')

    def test_database_errors_do_not_disclose_connection_details(self):
        with patch('config.checks.connections') as databases:
            database = databases['default']
            database.vendor = 'postgresql'
            database.cursor.side_effect = OperationalError('private connection credentials')
            errors = check_application_database_role(None, databases=['default'])
        self.assertEqual(errors[0].id, 'config.E001')
        self.assertNotIn('private', str(errors))

    def test_default_checks_and_development_do_not_open_database_connections(self):
        with patch('config.checks.connections') as databases:
            self.assertEqual(check_application_database_role(None), [])
            self.assertEqual(check_application_database_role(None, databases=['other']), [])
            with override_settings(IS_PRODUCTION=False):
                self.assertEqual(check_application_database_role(None, databases=['default']), [])
            databases.__getitem__.assert_not_called()
