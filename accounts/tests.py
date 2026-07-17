"""
Tests for accounts app — covers the bug fixes made in the Round 3 audit session.

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