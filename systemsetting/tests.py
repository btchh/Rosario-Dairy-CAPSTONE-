from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from .models import SystemSettings, NotificationSettings
from .runtime import get_runtime_settings

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
      response = self.client.get('/settings/system/')
      self.assertEqual(response.status_code, 200)
      self.assertEqual(response.data['currency'], 'PHP')
      self.assertEqual(response.data['timezone'], 'Asia/Manila')

    def test_staff_can_read_but_cannot_update(self):
        self.client.force_authenticate(user=self.staff)
        get_response = self.client.get('/settings/system/')
        patch_response = self.client.patch(
            '/settings/system/', {'currency': 'USD'}, format='json'
        )
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(patch_response.status_code, 403)

    def test_unauthenticated_cannot_access(self):
        response = self.client.get('/settings/system/')
        self.assertEqual(response.status_code, 401)

    def test_patch_updates_single_field(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch('/settings/system/', {'currency': 'usd'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['currency'], 'USD')
        self.assertEqual(response.data['system_name'], '')  # unrelated field untouched

    def test_old_detail_url_remains_compatible(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            '/settings/system/1/', {'currency': 'USD'}, format='json'
        )
        self.assertEqual(response.status_code, 200)

    def test_invalid_runtime_values_are_rejected(self):
        self.client.force_authenticate(user=self.admin)
        cases = [
            ({'currency': 'pesos'}, 'currency'),
            ({'timezone': 'Manila'}, 'timezone'),
            ({'date_format': 'Month Day Year'}, 'date_format'),
            ({'language': 'english'}, 'language'),
        ]
        for payload, field in cases:
            with self.subTest(field=field):
                response = self.client.patch('/settings/system/', payload, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)

    def test_runtime_cache_is_invalidated_after_update(self):
        self.assertEqual(get_runtime_settings()['currency'], 'PHP')
        config = SystemSettings.get_config()
        config.currency = 'USD'
        config.save()
        self.assertEqual(get_runtime_settings()['currency'], 'USD')

    def test_overview_bootstraps_both_setting_groups_for_staff(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/settings/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('system', response.data)
        self.assertIn('notifications', response.data)
        self.assertFalse(response.data['permissions']['can_manage_settings'])

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

    def test_staff_can_read_but_cannot_update(self):
        self.client.force_authenticate(user=self.staff)
        get_response = self.client.get('/settings/notifications/')
        patch_response = self.client.patch(
            '/settings/notifications/', {'low_stock_alerts': False}, format='json'
        )
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(patch_response.status_code, 403)

    def test_patch_toggles_single_field(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            '/settings/notifications/', {'report_ready_notifications': True}, format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['report_ready_notifications'])
        self.assertTrue(response.data['low_stock_alerts'])  # untouched

    def test_singleton_never_duplicates(self):
        NotificationSettings.get_config()
        NotificationSettings.get_config()
        self.assertEqual(NotificationSettings.objects.count(), 1)

    def test_disabled_low_stock_setting_suppresses_alert_feed(self):
        config = NotificationSettings.get_config()
        config.low_stock_alerts = False
        config.save()
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/inventory/low-stock/products/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_disabled_near_expiry_setting_suppresses_alert_feed(self):
        config = NotificationSettings.get_config()
        config.near_expiry_alerts = False
        config.save()
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/inventory/expiring/ingredients/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])
