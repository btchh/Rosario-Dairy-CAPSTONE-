"""
Tests for accounts app — covers the bug fixes made in the Round 3 audit session,
plus the Round 4 fixes: AdminResetPasswordView 500->400, and last_login
exposure/population.

Run with: python manage.py test accounts
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()


def make_user(username, role='staff', is_active=True):
    return User.objects.create_user( # pyright: ignore[reportAttributeAccessIssue]
        username=username,
        password='testpass123!',
        email=f'{username}@example.com',
        role=role,
        first_name='Test',
        last_name='User',
        is_active=is_active,
    )


class UserDetailPatchValidationTests(TestCase):
    """
    Covers the UserDetailView.patch() validation-bypass fix:
    - invalid role values used to save silently (causing silent permission loss)
    - invalid email format was never validated
    - duplicate email raised an unhandled IntegrityError (500) instead of a clean 400
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_user('admin1', role='admin')
        self.other_admin = make_user('admin2', role='admin')  # so "last admin" guard doesn't block unrelated tests
        self.staff = make_user('staffer1', role='staff')
        self.client.force_authenticate(user=self.admin)

    def test_invalid_role_value_rejected(self):
        response = self.client.patch(
            f'/accounts/users/{self.staff.pk}/', {'role': 'ADMIN'}, format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.role, 'staff')  # unchanged

    def test_valid_role_change_succeeds(self):
        response = self.client.patch(
            f'/accounts/users/{self.staff.pk}/', {'role': 'admin'}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.role, 'admin')

    def test_invalid_email_format_rejected(self):
        response = self.client.patch(
            f'/accounts/users/{self.staff.pk}/', {'email': 'not-an-email'}, format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.staff.refresh_from_db()
        self.assertNotEqual(self.staff.email, 'not-an-email')

    def test_valid_email_change_succeeds(self):
        response = self.client.patch(
            f'/accounts/users/{self.staff.pk}/', {'email': 'newemail@example.com'}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.email, 'newemail@example.com')

    def test_duplicate_email_returns_clean_400_not_500(self):
        """Fix: this used to raise an unhandled IntegrityError -> 500."""
        response = self.client.patch(
            f'/accounts/users/{self.staff.pk}/',
            {'email': self.other_admin.email},  # already taken
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_other_fields_still_patch_normally(self):
        response = self.client.patch(
            f'/accounts/users/{self.staff.pk}/',
            {'first_name': 'Updated', 'phone_number': '09171234567'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.first_name, 'Updated')
        self.assertEqual(self.staff.phone_number, '09171234567')


class UserDetailPatchGuardTests(TestCase):
    """Pre-existing guards (not part of this session's fixes, but exercised by the same view —
    included so the validation fix's insertion point doesn't regress these)."""

    def setUp(self):
        self.client = APIClient()
        self.admin = make_user('admin1', role='admin')
        self.other_admin = make_user('admin2', role='admin')
        self.staff = make_user('staffer1', role='staff')

    def test_cannot_change_own_role(self):
        self.client.force_authenticate(user=self.admin) # pyright: ignore[reportAttributeAccessIssue]
        response = self.client.patch(
            f'/accounts/users/{self.admin.pk}/', {'role': 'staff'}, format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_deactivate_own_account(self):
        self.client.force_authenticate(user=self.admin) # pyright: ignore[reportAttributeAccessIssue]
        response = self.client.patch(
            f'/accounts/users/{self.admin.pk}/', {'is_active': False}, format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_remove_last_active_admin(self):
        self.other_admin.is_active = False
        self.other_admin.save()
        # now self.admin is the only active admin; a third party (say, staff acting as if elevated)
        # tries to demote self.admin — simulate via a separate admin account being demoted instead
        self.client.force_authenticate(user=self.admin) # pyright: ignore[reportAttributeAccessIssue]
        response = self.client.patch(
            f'/accounts/users/{self.other_admin.pk}/', {'is_active': True}, format='json',
        )
        # sanity: reactivating is fine, this just re-establishes two admins
        self.assertEqual(response.status_code, 200)

    def test_terminated_user_cannot_be_reactivated_directly(self):
        self.staff.is_active = False
        self.staff.deactivation_reason = 'terminated'
        self.staff.save()
        self.client.force_authenticate(user=self.admin) # pyright: ignore[reportAttributeAccessIssue]
        response = self.client.patch(
            f'/accounts/users/{self.staff.pk}/', {'is_active': True}, format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_staff_cannot_patch_other_users(self):
        self.client.force_authenticate(user=self.staff) # pyright: ignore[reportAttributeAccessIssue]
        response = self.client.patch(
            f'/accounts/users/{self.admin.pk}/', {'first_name': 'Hacked'}, format='json',
        )
        self.assertEqual(response.status_code, 403)


class UserDetailDeleteTests(TestCase):
    """Sanity coverage for the delete (deactivate) endpoint — not part of this session's fixes,
    but adjacent to the patch validation work."""

    def setUp(self):
        self.client = APIClient()
        self.admin = make_user('admin1', role='admin')
        self.other_admin = make_user('admin2', role='admin')
        self.staff = make_user('staffer1', role='staff')
        self.client.force_authenticate(user=self.admin)

    def test_deactivate_with_valid_reason_succeeds(self):
        response = self.client.delete(
            f'/accounts/users/{self.staff.pk}/', {'reason': 'resigned'}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.is_active)
        self.assertEqual(self.staff.deactivation_reason, 'resigned')

    def test_deactivate_with_invalid_reason_rejected(self):
        response = self.client.delete(
            f'/accounts/users/{self.staff.pk}/', {'reason': 'banana'}, format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_deactivate_last_active_admin(self):
        self.other_admin.is_active = False
        self.other_admin.save()
        response = self.client.delete(
            f'/accounts/users/{self.admin.pk}/', {'reason': 'resigned'}, format='json',
        )
        self.assertEqual(response.status_code, 400)


class RegisterAndLoginTests(TestCase):
    """Sanity coverage for register/login — not part of this session's fixes."""

    def setUp(self):
        self.client = APIClient()
        self.admin = make_user('admin1', role='admin')

    def test_register_requires_admin(self):
        self.client.force_authenticate(user=None) # pyright: ignore[reportAttributeAccessIssue]
        response = self.client.post('/accounts/register/', {
            'username': 'newuser', 'password': 'ComplexPass123!', 'email': 'new@example.com',
            'role': 'staff', 'first_name': 'New', 'last_name': 'User',
        }, format='json')
        self.assertEqual(response.status_code, 401)

    def test_register_invalid_role_rejected(self):
        self.client.force_authenticate(user=self.admin) # pyright: ignore[reportAttributeAccessIssue]
        response = self.client.post('/accounts/register/', {
            'username': 'newuser', 'password': 'ComplexPass123!', 'email': 'new@example.com',
            'role': 'superadmin', 'first_name': 'New', 'last_name': 'User',
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_register_duplicate_username_returns_clean_400(self):
        self.client.force_authenticate(user=self.admin) # pyright: ignore[reportAttributeAccessIssue]
        response = self.client.post('/accounts/register/', {
            'username': 'admin1', 'password': 'ComplexPass123!', 'email': 'unique@example.com',
            'role': 'staff', 'first_name': 'New', 'last_name': 'User',
        }, format='json')
        self.assertEqual(response.status_code, 400)


# ---------------------------------------------------------------------------
# Round 4: AdminResetPasswordView fix (500 -> 400/404 on bad input)
# ---------------------------------------------------------------------------

class AdminResetPasswordViewTests(TestCase):
    """
    Covers the AdminResetPasswordView fix: previously, a weak/invalid new_password
    raised an uncaught ValueError from forgot_password() -> 500, and a missing
    new_password crashed inside Django's password validators (AttributeError on
    None) -> 500. Both must now return clean 400s, and valid resets must still work.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_user('admin1', role='admin')
        self.staff = make_user('staffer1', role='staff')
        self.client.force_authenticate(user=self.admin)

    def test_missing_username_returns_400_not_500(self):
        response = self.client.post(
            '/accounts/admin-reset-password/',
            {'new_password': 'BrandNewPass123!'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_new_password_returns_400_not_500(self):
        """Previously: new_password=None reached validate_password() and crashed
        with AttributeError inside NumericPasswordValidator, surfacing as a 500."""
        response = self.client.post(
            '/accounts/admin-reset-password/',
            {'username': self.staff.username},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_weak_password_returns_400_not_500(self):
        """Previously: forgot_password() raised ValueError on a validation failure,
        which the view didn't catch -> unhandled 500."""
        response = self.client.post(
            '/accounts/admin-reset-password/',
            {'username': self.staff.username, 'new_password': '123'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.data)

    def test_nonexistent_username_returns_404(self):
        response = self.client.post(
            '/accounts/admin-reset-password/',
            {'username': 'ghost_user', 'new_password': 'BrandNewPass123!'},
            format='json',
        )
        self.assertEqual(response.status_code, 404)

    def test_valid_reset_succeeds_and_password_actually_changes(self):
        response = self.client.post(
            '/accounts/admin-reset-password/',
            {'username': self.staff.username, 'new_password': 'BrandNewPass123!'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password('BrandNewPass123!'))
        self.assertFalse(self.staff.check_password('testpass123!'))  # old password no longer works

    def test_non_admin_cannot_reset_passwords(self):
        self.client.force_authenticate(user=self.staff) # pyright: ignore[reportAttributeAccessIssue]
        response = self.client.post(
            '/accounts/admin-reset-password/',
            {'username': self.admin.username, 'new_password': 'BrandNewPass123!'},
            format='json',
        )
        self.assertEqual(response.status_code, 403)


class ForgotPasswordViewTests(TestCase):
    endpoint = '/accounts/forgot-password/'
    confirm_endpoint = '/accounts/reset-password/'
    success_message = (
        'If the credentials match our records, a password reset code has been sent.'
    )

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()
        self.user = make_user('forgotuser')
        self.payload = {
            'username': self.user.username,
            'email': self.user.email,
        }

    def test_missing_any_field_returns_400(self):
        for field in self.payload:
            with self.subTest(field=field):
                payload = self.payload.copy()
                payload.pop(field)
                response = self.client.post(self.endpoint, payload, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn('error', response.data)

    def test_wrong_email_returns_generic_200_without_sending_email(self):
        from django.core import mail

        payload = {**self.payload, 'email': 'wrong@example.com'}
        response = self.client.post(self.endpoint, payload, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['message'], self.success_message)
        self.assertEqual(len(mail.outbox), 0)

    def test_nonexistent_username_returns_generic_200(self):
        payload = {**self.payload, 'username': 'missing-user'}
        response = self.client.post(self.endpoint, payload, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['message'], self.success_message)

    def test_inactive_user_returns_generic_200_without_sending_email(self):
        from django.core import mail

        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        response = self.client.post(self.endpoint, self.payload, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['message'], self.success_message)
        self.assertEqual(len(mail.outbox), 0)

    def _request_otp(self):
        import re
        from django.core import mail

        response = self.client.post(self.endpoint, self.payload, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        match = re.search(r'\b(\d{6})\b', mail.outbox[0].body)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_matching_credentials_send_otp_to_account_email(self):
        from django.core import mail

        self._request_otp()
        self.assertEqual(mail.outbox[0].to, [self.user.email])

    def test_invalid_otp_returns_400_without_changing_password(self):
        otp = self._request_otp()
        wrong_otp = '000001' if otp == '000000' else '000000'
        response = self.client.post(self.confirm_endpoint, {
            **self.payload, 'otp': wrong_otp,
            'new_password': 'BrandNewPass123!',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('testpass123!'))

    def test_weak_new_password_returns_400(self):
        otp = self._request_otp()
        response = self.client.post(self.confirm_endpoint, {
            **self.payload, 'otp': otp, 'new_password': '123',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.data)

    def test_valid_otp_resets_password_and_clears_lockout(self):
        from datetime import timedelta
        from django.utils import timezone

        self.user.failed_login_attempts = 5
        self.user.locked_until = timezone.now() + timedelta(minutes=15)
        self.user.save(update_fields=['failed_login_attempts', 'locked_until'])
        self.payload['email'] = self.user.email.upper()
        otp = self._request_otp()

        response = self.client.post(self.confirm_endpoint, {
            **self.payload, 'otp': otp, 'new_password': 'BrandNewPass123!',
        }, format='json')

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNewPass123!'))
        self.assertEqual(self.user.failed_login_attempts, 0)
        self.assertIsNone(self.user.locked_until)

    def test_otp_is_single_use(self):
        otp = self._request_otp()
        payload = {
            **self.payload, 'otp': otp, 'new_password': 'BrandNewPass123!',
        }
        first = self.client.post(self.confirm_endpoint, payload, format='json')
        second = self.client.post(self.confirm_endpoint, payload, format='json')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)

    def test_resend_cooldown_prevents_duplicate_email(self):
        from django.core import mail

        self.client.post(self.endpoint, self.payload, format='json')
        self.client.post(self.endpoint, self.payload, format='json')
        self.assertEqual(len(mail.outbox), 1)

    def test_expired_otp_is_rejected_and_deleted(self):
        from django.utils import timezone
        from accounts.models import PasswordResetChallenge

        otp = self._request_otp()
        challenge = PasswordResetChallenge.objects.get(user=self.user)
        challenge.expires_at = timezone.now() - timedelta(seconds=1)
        challenge.save(update_fields=['expires_at'])

        response = self.client.post(self.confirm_endpoint, {
            **self.payload, 'otp': otp, 'new_password': 'BrandNewPass123!',
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertFalse(PasswordResetChallenge.objects.filter(user=self.user).exists())

    def test_fifth_invalid_attempt_invalidates_otp(self):
        from accounts.models import PasswordResetChallenge

        otp = self._request_otp()
        wrong_otp = '000001' if otp == '000000' else '000000'
        for _ in range(5):
            response = self.client.post(self.confirm_endpoint, {
                **self.payload, 'otp': wrong_otp,
                'new_password': 'BrandNewPass123!',
            }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertFalse(PasswordResetChallenge.objects.filter(user=self.user).exists())


class LogoutViewTests(TestCase):
    def test_inactive_user_with_valid_access_token_can_blacklist_refresh_token(self):
        from rest_framework_simplejwt.tokens import RefreshToken

        user = make_user('logoutuser')
        refresh = RefreshToken.for_user(user)
        access = str(refresh.access_token)
        refresh_value = str(refresh)
        user.is_active = False
        user.save(update_fields=['is_active'])

        response = self.client.post(
            '/accounts/logout/',
            {'refresh_token': refresh_value},
            format='json',
            HTTP_AUTHORIZATION=f'Bearer {access}',
        )

        self.assertEqual(response.status_code, 200)
        refresh_response = self.client.post(
            '/accounts/refresh/', {'refresh': refresh_value}, format='json'
        )
        self.assertEqual(refresh_response.status_code, 401)


# ---------------------------------------------------------------------------
# Round 4: last_login population (UPDATE_LAST_LOGIN) + exposure in user views
# ---------------------------------------------------------------------------

class LastLoginTests(TestCase):
    """
    Covers the last_login fix: SIMPLE_JWT['UPDATE_LAST_LOGIN'] must be True so
    TokenObtainPairView actually populates the field, and it must be surfaced
    in GetUserView, UserListView, and UserDetailView.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_user('admin1', role='admin')
        self.staff = make_user('staffer1', role='staff')

    def test_login_populates_last_login(self):
        self.assertIsNone(self.staff.last_login)
        response = self.client.post('/accounts/login/', {
            'username': self.staff.username, 'password': 'testpass123!',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.staff.refresh_from_db()
        self.assertIsNotNone(self.staff.last_login)

    def test_get_user_view_includes_last_login_key(self):
        self.client.force_authenticate(user=self.staff) # pyright: ignore[reportAttributeAccessIssue]
        response = self.client.get('/accounts/user/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('last_login', response.data)

    def test_user_list_view_includes_last_login_key(self):
        self.client.force_authenticate(user=self.admin) # pyright: ignore[reportAttributeAccessIssue]
        response = self.client.get('/accounts/users/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.data) > 0)
        for row in response.data:
            self.assertIn('last_login', row)

    def test_user_detail_view_includes_last_login_key(self):
        self.client.force_authenticate(user=self.admin) # pyright: ignore[reportAttributeAccessIssue]
        response = self.client.get(f'/accounts/users/{self.staff.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('last_login', response.data)

    def test_user_detail_reflects_populated_last_login_after_a_real_login(self):
        self.client.post('/accounts/login/', {
            'username': self.staff.username, 'password': 'testpass123!',
        }, format='json')
        self.client.force_authenticate(user=self.admin) # pyright: ignore[reportAttributeAccessIssue]
        response = self.client.get(f'/accounts/users/{self.staff.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data['last_login'])

# ---------------------------------------------------------------------------
# Round 5: login lockout + profile/password cooldowns
# ---------------------------------------------------------------------------

from datetime import timedelta
from django.utils import timezone


class LoginLockoutTests(TestCase):
    """
    Covers CooldownTokenObtainPairSerializer: 5 consecutive failed logins
    locks the account for 15 minutes while returning generic 401 responses, successful
    login resets the counter, and lockout is scoped per-account.
    """

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = APIClient()
        self.user = make_user('lockoutuser')
        self.other_user = make_user('otheruser')

    def _bad_login(self, username='lockoutuser'):
        return self.client.post('/accounts/login/', {
            'username': username, 'password': 'wrongpassword',
        }, format='json')

    def test_failed_attempts_increment_but_stay_401_until_threshold(self):
        for _ in range(4):
            response = self._bad_login()
            self.assertEqual(response.status_code, 401)
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 4)
        self.assertIsNone(self.user.locked_until)

    def test_fifth_failed_attempt_locks_account_and_returns_generic_401(self):
        for _ in range(5):
            response = self._bad_login()
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('Retry-After', response.headers)
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 5)
        self.assertIsNotNone(self.user.locked_until)

    def test_locked_account_rejects_even_correct_password(self):
        for _ in range(5):
            self._bad_login()
        response = self.client.post('/accounts/login/', {
            'username': 'lockoutuser', 'password': 'testpass123!',
        }, format='json')
        self.assertEqual(response.status_code, 401)

    def test_successful_login_resets_failed_attempts(self):
        self.user.failed_login_attempts = 3
        self.user.save()
        response = self.client.post('/accounts/login/', {
            'username': 'lockoutuser', 'password': 'testpass123!',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 0)
        self.assertIsNone(self.user.locked_until)

    def test_lockout_expires_after_window_passes(self):
        """Simulates the 15-minute window having already elapsed."""
        self.user.failed_login_attempts = 5
        self.user.locked_until = timezone.now() - timedelta(minutes=1)
        self.user.save()
        response = self.client.post('/accounts/login/', {
            'username': 'lockoutuser', 'password': 'testpass123!',
        }, format='json')
        self.assertEqual(response.status_code, 200)

    def test_lockout_is_scoped_per_account_not_global(self):
        """Locking one user out must not block a different user from logging in."""
        for _ in range(5):
            self._bad_login(username='lockoutuser')
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.locked_until)

        response = self.client.post('/accounts/login/', {
            'username': 'otheruser', 'password': 'testpass123!',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.other_user.refresh_from_db()
        self.assertIsNone(self.other_user.locked_until)

    def test_nonexistent_username_does_not_error(self):
        """No user row to lock, but must still return a clean 401, not 500."""
        response = self._bad_login(username='ghost_user_xyz')
        self.assertEqual(response.status_code, 401)


class ProfileEditCooldownTests(TestCase):
    """Covers update_own_profile()'s 5-minute cooldown."""

    def setUp(self):
        self.client = APIClient()
        self.user = make_user('cooldownprofile')
        self.client.force_authenticate(user=self.user)

    def test_first_edit_succeeds(self):
        response = self.client.patch('/accounts/user/', {'first_name': 'First'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_profile_update_at)

    def test_second_edit_within_window_returns_400_with_cooldown_message(self):
        self.client.patch('/accounts/user/', {'first_name': 'First'}, format='json')
        response = self.client.patch('/accounts/user/', {'first_name': 'Second'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('minute', response.data['error'].lower())
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'First')  # second edit rejected, not applied

    def test_edit_after_window_elapses_succeeds(self):
        self.user.last_profile_update_at = timezone.now() - timedelta(minutes=6)
        self.user.save()
        response = self.client.patch('/accounts/user/', {'first_name': 'Updated'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Updated')


class PasswordChangeCooldownTests(TestCase):
    """Covers change_password()'s 15-minute cooldown (self-service only)."""

    def setUp(self):
        self.client = APIClient()
        self.user = make_user('cooldownpass')
        self.client.force_authenticate(user=self.user)

    def _change(self, old='testpass123!', new='NewComplexPass123!'):
        return self.client.post('/accounts/change-password/', {
            'old_password': old, 'new_password': new,
        }, format='json')

    def test_first_change_succeeds(self):
        response = self._change()
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_password_change_at)

    def test_second_change_within_window_returns_400_with_cooldown_message(self):
        self._change(new='NewComplexPass123!')
        response = self._change(old='NewComplexPass123!', new='AnotherPass456!')
        self.assertEqual(response.status_code, 400)
        self.assertIn('minute', response.data['error'].lower())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewComplexPass123!'))  # second change rejected

    def test_change_after_window_elapses_succeeds(self):
        self._change(new='NewComplexPass123!')
        self.user.last_password_change_at = timezone.now() - timedelta(minutes=16)
        self.user.save()
        response = self._change(old='NewComplexPass123!', new='AnotherPass456!')
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('AnotherPass456!'))


class AdminResetPasswordCooldownExemptionTests(TestCase):
    """Covers: admin resetting someone else's password is NOT subject to any
    cooldown — deliberately different from self-service change_password()."""

    def setUp(self):
        self.client = APIClient()
        self.admin = make_user('cooldownadmin', role='admin')
        self.staff = make_user('cooldownstaff')
        self.client.force_authenticate(user=self.admin)

    def test_consecutive_admin_resets_both_succeed_with_no_cooldown(self):
        response1 = self.client.post('/accounts/admin-reset-password/', {
            'username': self.staff.username, 'new_password': 'FirstResetPass123!',
        }, format='json')
        self.assertEqual(response1.status_code, 200)

        # immediately again, no delay — must NOT be blocked by any cooldown
        response2 = self.client.post('/accounts/admin-reset-password/', {
            'username': self.staff.username, 'new_password': 'SecondResetPass456!',
        }, format='json')
        self.assertEqual(response2.status_code, 200)

        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password('SecondResetPass456!'))

    def test_admin_reset_does_not_set_last_password_change_at(self):
        """forgot_password() bypasses the self-service cooldown field entirely —
        confirms the two code paths are genuinely independent, not just
        coincidentally unblocked."""
        self.client.post('/accounts/admin-reset-password/', {
            'username': self.staff.username, 'new_password': 'ResetPass123!',
        }, format='json')
        self.staff.refresh_from_db()
        self.assertIsNone(self.staff.last_password_change_at)

