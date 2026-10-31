"""Use real JWT authentication to distinguish human activity from background IO."""
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.db import OperationalError
from django.test import override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from accounts.models import LoginSession, Users
from accounts.services.session_service import create_session_token
from config.test_support import APITestCase


@override_settings(
    AUTH_IDLE_TIMEOUT_SECONDS=900, AUTH_IDLE_WARNING_SECONDS=60,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class IdleSessionTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = Users.objects.create_user(username='idle-staff', role='staff',
            email='idle@example.test', password='IdleTestPassword!43')
        self.token = create_session_token(self.user)
        self.session = LoginSession.objects.get(pk=self.token['sid'])
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token.access_token}')

    def at(self, moment):
        return patch('accounts.services.session_service.timezone.now', return_value=moment)

    def assert_idle(self, response):
        self.assertEqual(response.status_code, 401, response.data)
        self.assertEqual(response.data['code'], 'session_idle_timeout')

    def test_login_issues_distinct_sessions_with_matching_access_and_refresh_claims(self):
        for role in ('admin', 'staff'):
            self.user.role = role
            self.user.save(update_fields=['role'])
            response = self.client.post('/accounts/login/', {
                'username': self.user.username, 'password': 'IdleTestPassword!43'}, format='json')
            self.assertEqual(response.status_code, 200)
            refresh = RefreshToken(response.data['refresh'])
            self.assertNotEqual(refresh['sid'], self.token['sid'])
            self.assertEqual(refresh['sid'], response.data['session']['id'])
            self.assertEqual(refresh.access_token['sid'], refresh['sid'])
            self.assertEqual(response.data['session']['idle_timeout_seconds'], 900)
            self.assertEqual(response.data['session']['warning_seconds'], 60)

    def test_background_reads_status_and_refresh_do_not_extend_idle_deadline(self):
        before = self.session.idle_expires_at
        with self.at(before - timedelta(seconds=1)):
            self.assertEqual(self.client.get('/accounts/user/').status_code, 200)
            status = self.client.get('/accounts/session/')
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status['Cache-Control'], 'no-store')
            response = self.client.post('/accounts/refresh/', {'refresh': str(self.token)}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RefreshToken(response.data['refresh'])['sid'], str(self.session.pk))
        self.assertEqual(response.data['session']['idle_expires_at'], before.isoformat())
        self.session.refresh_from_db()
        self.assertEqual(self.session.idle_expires_at, before)
        with self.at(before):
            self.assert_idle(self.client.post('/accounts/refresh/',
                {'refresh': response.data['refresh']}, format='json'))

    def test_access_refresh_and_activity_fail_at_exact_deadline_without_resurrection(self):
        original_activity = self.session.last_activity_at
        outstanding = OutstandingToken.objects.count()
        with self.at(self.session.idle_expires_at):
            self.assert_idle(self.client.get('/accounts/user/'))
            self.assert_idle(self.client.get('/accounts/session/'))
            self.assert_idle(self.client.post('/accounts/refresh/', {'refresh': str(self.token)}, format='json'))
            self.assert_idle(self.client.post('/accounts/session/activity/', {}, format='json'))
        with self.at(self.session.idle_expires_at + timedelta(days=1)):
            self.assert_idle(self.client.post('/accounts/session/activity/', {}, format='json'))
        self.session.refresh_from_db()
        self.assertEqual(self.session.last_activity_at, original_activity)
        self.assertEqual(OutstandingToken.objects.count(), outstanding)

    def test_activity_extends_only_its_session_using_server_time(self):
        other = create_session_token(self.user)
        other_session = LoginSession.objects.get(pk=other['sid'])
        original_other_deadline = other_session.idle_expires_at
        now = self.session.last_activity_at + timedelta(minutes=10)
        with self.at(now):
            response = self.client.post('/accounts/session/activity/', {
                'last_activity_at': '2099-01-01T00:00:00Z',
                'idle_expires_at': '2099-01-01T00:00:00Z',
                'id': str(other_session.pk)}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.session.refresh_from_db()
        other_session.refresh_from_db()
        self.assertEqual(self.session.last_activity_at, now)
        self.assertEqual(self.session.idle_expires_at, now + timedelta(minutes=15))
        self.assertEqual(other_session.idle_expires_at, original_other_deadline)
        with self.at(original_other_deadline):
            self.assertEqual(self.client.get('/accounts/user/').status_code, 200)

    def test_legacy_missing_malformed_unknown_and_wrong_owner_session_cannot_authenticate_or_refresh(self):
        other_user = Users.objects.create_user(username='idle-other', email='other@example.test')
        other = create_session_token(other_user)
        for sid in (None, 'invalid', str(uuid4()), other['sid']):
            token = RefreshToken.for_user(self.user)
            token['token_version'] = self.user.token_version
            if sid is not None:
                token['sid'] = sid
            self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')
            with self.subTest(sid=sid):
                response = self.client.get('/accounts/user/')
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.data['code'], 'session_required')
                response = self.client.post('/accounts/refresh/', {'refresh': str(token)}, format='json')
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.data['code'], 'session_required')

    def test_fresh_login_works_after_idle_expiry_without_reviving_old_session(self):
        LoginSession.objects.filter(pk=self.session.pk).update(idle_expires_at=timezone.now())
        response = self.client.post('/accounts/login/', {
            'username': self.user.username, 'password': 'IdleTestPassword!43'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assert_idle(self.client.get('/accounts/user/'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {response.data["access"]}')
        self.assertEqual(self.client.get('/accounts/user/').status_code, 200)

    def test_idle_user_cannot_execute_business_write(self):
        with self.at(self.session.idle_expires_at), \
                patch('sales.views.checkout_views.SalesService.checkout') as checkout:
            self.assert_idle(self.client.post('/sales/checkout/', {}, format='json'))
        checkout.assert_not_called()

    def test_activity_rechecks_deadline_after_initial_authentication(self):
        from accounts.services.session_service import check_session

        def authenticate_then_expire(user, token, **kwargs):
            session = check_session(user, token, **kwargs)
            if not kwargs.get('for_update'):
                LoginSession.objects.filter(pk=session.pk).update(idle_expires_at=timezone.now())
            return session

        with patch('accounts.services.session_service.check_session', side_effect=authenticate_then_expire):
            self.assert_idle(self.client.post('/accounts/session/activity/', {}, format='json'))
        self.session.refresh_from_db()
        self.assertLessEqual(self.session.idle_expires_at, timezone.now())

    def test_idle_session_can_still_log_out_and_revoke_remaining_account_tokens(self):
        other = create_session_token(self.user)
        with self.at(self.session.idle_expires_at):
            response = self.client.post('/accounts/logout/',
                {'refresh_token': str(self.token)}, format='json')
        self.assertEqual(response.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other.access_token}')
        self.assertEqual(self.client.get('/accounts/user/').status_code, 401)

    def test_activity_requires_authentication_and_an_object_body(self):
        response = self.client.post('/accounts/session/activity/', [], format='json')
        self.assertEqual(response.status_code, 400)
        self.client.credentials()
        self.assertEqual(self.client.get('/accounts/session/').status_code, 401)
        self.assertEqual(self.client.post('/accounts/session/activity/', {}, format='json').status_code, 401)

    def test_unavailable_session_store_fails_closed_with_safe_503(self):
        with patch('accounts.services.session_service.LoginSession.objects.filter',
                   side_effect=OperationalError('private database connection')), \
                self.assertLogs('config.exceptions', level='ERROR'):
            response = self.client.get('/accounts/user/')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', str(response.data))

    def test_failed_activity_write_does_not_extend_deadline(self):
        deadline = self.session.idle_expires_at
        with patch.object(LoginSession, 'save', side_effect=OperationalError('private database connection')), \
                self.assertLogs('config.exceptions', level='ERROR'):
            response = self.client.post('/accounts/session/activity/', {}, format='json')
        self.assertEqual(response.status_code, 503)
        self.session.refresh_from_db()
        self.assertEqual(self.session.idle_expires_at, deadline)
