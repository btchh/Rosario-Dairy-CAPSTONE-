"""Regressions for concurrent admin changes and logout availability errors."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local
from unittest.mock import patch

from django.db import InterfaceError, OperationalError, connections
from django.db.models.query import QuerySet
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework_simplejwt.exceptions import TokenError

from accounts.models import Users
from accounts.services.admin_service import update_user
from accounts.views.logout_views import LogoutView


class LogoutAvailabilityTests(SimpleTestCase):
    def response_for(self, error):
        request = APIRequestFactory().post('/accounts/logout/',
            {'refresh_token': 'probe-token'}, format='json')
        force_authenticate(request, user=Users(pk=1, username='logout-probe', role='staff'))
        with patch('accounts.views.logout_views.user_service.logout', side_effect=error) as logout:
            response = LogoutView.as_view()(request)
        logout.assert_called_once()
        return response

    def test_database_outage_returns_safe_503_without_retrying_logout(self):
        for error in (OperationalError, InterfaceError):
            with self.subTest(error=error), self.assertLogs('config.exceptions', level='ERROR'):
                response = self.response_for(error('private database details'))
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.data['detail'].code, 'database_unavailable')
            self.assertNotIn('private', str(response.data))

    def test_expected_token_rejections_preserve_existing_400_response(self):
        for error in (TokenError, ValueError, AuthenticationFailed):
            with self.subTest(error=error):
                response = self.response_for(error('invalid token'))
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.data, {'error': 'Invalid or expired refresh token.'})

    def test_programming_errors_are_not_reported_as_invalid_tokens(self):
        with self.assertRaisesMessage(RuntimeError, 'unexpected bug'):
            self.response_for(RuntimeError('unexpected bug'))


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                                      'LOCATION': 'admin-lock-audit'}})
class ConcurrentAdminMutationTests(TransactionTestCase):
    def setUp(self):
        self.admin_a = Users.objects.create_user(username='lock-admin-a',
            email='lock-admin-a@example.test', role='admin')
        self.admin_b = Users.objects.create_user(username='lock-admin-b',
            email='lock-admin-b@example.test', role='admin')

    def concurrent_demotions(self, requesting_user=None):
        ready = Barrier(2)
        state = local()
        original_get, original_iter = QuerySet.get, QuerySet.__iter__

        def locking_iteration(queryset):
            result = original_iter(queryset)
            if queryset.model is Users and queryset.query.select_for_update:
                state.admin_rows_locked = True
            return result

        def target_get(queryset, *args, **kwargs):
            result = original_get(queryset, *args, **kwargs)
            if (queryset.model is Users and queryset.query.select_for_update
                    and kwargs.get('pk') in (self.admin_a.pk, self.admin_b.pk)
                    and not getattr(state, 'admin_rows_locked', False)):
                # Reproduce the old inversion by letting both targets lock
                # before either attempts to lock the other active admins.
                ready.wait(timeout=10)
            return result

        def demote(target):
            try:
                requester = requesting_user or (
                    self.admin_b if target.pk == self.admin_a.pk else self.admin_a
                )
                update_user(target.pk, {'role': 'staff'}, requester)
                return 'committed'
            except ValueError as exc:
                return str(exc)
            finally:
                connections.close_all()

        with patch.object(QuerySet, 'get', target_get), \
                patch.object(QuerySet, '__iter__', locking_iteration), \
                ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(demote, [self.admin_a, self.admin_b]))

    def test_concurrent_admin_demotions_commit_without_deadlock(self):
        operator = Users.objects.create_user(username='lock-operator',
            email='lock-operator@example.test', role='admin')
        self.assertEqual(self.concurrent_demotions(operator), ['committed', 'committed'])
        self.assertEqual(Users.objects.filter(role='admin', is_active=True).count(), 1)

    def test_concurrent_demotions_preserve_last_active_admin(self):
        # Both requests start with admin authorization and target one another.
        results = self.concurrent_demotions()
        self.assertEqual(sorted(results), ['Cannot remove the last active admin.', 'committed'])
        self.assertEqual(Users.objects.filter(role='admin', is_active=True).count(), 1)
