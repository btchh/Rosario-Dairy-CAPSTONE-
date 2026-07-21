from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from .models import SystemSettings, NotificationSettings

User = get_user_model()


def make_user(username='staffuser', role='staff'):
    return User.objects.create_user(
        username=username,
        password='testpass123!',
        email=f'{username}@example.com',
        role=role,
        first_name='Test',
        last_name='User',
    )


class SystemSettingsViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_user('admin1', role='admin')
        self.staff = make_user('staffer1', role='staff')

    def test_get_returns_defaults(self):
      self.client.force_authenticate(user=self.admin)
      response = self.client.get('/settings/notifications/')
      self.assertEqual(response.status_code, 200)
      self.assertTrue(response.data['low_stock_alerts'])
      self.assertTrue(response.data['near_expiry_alerts'])
      self.assertFalse(response.data['report_ready_notifications'])

    def test_staff_cannot_access(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/settings/system/')
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_cannot_access(self):
        response = self.client.get('/settings/system/')
        self.assertEqual(response.status_code, 401)

    def test_patch_updates_single_field(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch('/settings/system/1/', {'currency': 'USD'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['currency'], 'USD')
        self.assertEqual(response.data['system_name'], '')  # unrelated field untouched

    def test_singleton_never_duplicates(self):
        SystemSettings.get_config()
        SystemSettings.get_config()
        self.assertEqual(SystemSettings.objects.count(), 1)

    def test_post_not_allowed(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/settings/system/', {}, format='json')
        self.assertEqual(response.status_code, 405)

    def test_delete_not_allowed(self):
        self.client.force_authenticate(user=self.admin)
        SystemSettings.get_config()
        response = self.client.delete('/settings/system/1/')
        self.assertEqual(response.status_code, 405)


class NotificationSettingsViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_user('admin2', role='admin')
        self.staff = make_user('staffer2', role='staff')

    def test_get_returns_defaults(self):
      self.client.force_authenticate(user=self.admin)
      response = self.client.get('/settings/notifications/')
      self.assertEqual(response.status_code, 200)
      self.assertTrue(response.data['low_stock_alerts'])
      self.assertTrue(response.data['near_expiry_alerts'])
      self.assertFalse(response.data['report_ready_notifications'])

    def test_staff_cannot_access(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/settings/notifications/')
        self.assertEqual(response.status_code, 403)

    def test_patch_toggles_single_field(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            '/settings/notifications/1/', {'report_ready_notifications': True}, format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['report_ready_notifications'])
        self.assertTrue(response.data['low_stock_alerts'])  # untouched

    def test_singleton_never_duplicates(self):
        NotificationSettings.get_config()
        NotificationSettings.get_config()
        self.assertEqual(NotificationSettings.objects.count(), 1)