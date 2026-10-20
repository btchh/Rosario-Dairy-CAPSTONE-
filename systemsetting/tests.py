"""System settings, inventory thresholds and persistent notification tests.

Run all app tests: python manage.py test systemsetting
Run one class: python manage.py test systemsetting.tests.<TestClass>
PostgreSQL is required for the concurrency and database-locking checks.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Users
from inventory.models import Category, FEFOConf, Ingredient, IngredientBatch, Product, ProductBatch
from inventory.services.batch_service import BatchService
from sales.models import Customer
from sales.services import SalesService
from systemsetting.middleware import RuntimeSettingsMiddleware
from systemsetting.models import NotificationSettings, NotificationState, SystemSettings
from systemsetting.runtime import get_runtime_settings


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


# Core application behavior
# ----------------------------------------------------------------------------

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

    def test_staff_cannot_access_system_settings(self):
        config = SystemSettings.get_config()
        config.business_name = 'Private business'
        config.tin = 'private-tax-id'
        config.save()
        self.client.force_authenticate(user=self.staff)
        for url in ('/settings/system/', '/settings/system/1/'):
            for method in ('get', 'head', 'options', 'put', 'patch'):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(
                        url, {'currency': 'USD'}, format='json'
                    )
                    self.assertEqual(response.status_code, 403)
                    self.assertNotIn('Private business', str(response.data))
                    self.assertNotIn('private-tax-id', str(response.data))
        config.refresh_from_db()
        self.assertEqual(config.currency, 'PHP')

    def test_unauthenticated_cannot_access(self):
        for url in ('/settings/', '/settings/system/', '/settings/system/1/'):
            for method in ('get', 'head', 'options', 'put', 'patch'):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url)
                    self.assertEqual(response.status_code, 401)

    def test_admin_can_access_both_system_settings_urls(self):
        self.client.force_authenticate(user=self.admin)
        for url in ('/settings/system/', '/settings/system/1/'):
            payload = self.client.get(url).data
            payload['currency'] = 'USD'
            payload['system_name'] = 'Private business system'
            for method in ('get', 'head', 'options', 'put', 'patch'):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url, payload, format='json')
                    self.assertEqual(response.status_code, 200, response.data)

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

    def test_runtime_settings_fall_back_to_database_when_cache_is_down(self):
        config = SystemSettings.get_config()
        config.currency = 'USD'
        config.save()
        with patch('systemsetting.runtime.cache.get', side_effect=ConnectionError), \
             patch('systemsetting.runtime.cache.set', side_effect=ConnectionError), \
             self.assertLogs('systemsetting.runtime', level='ERROR'):
            self.assertEqual(get_runtime_settings()['currency'], 'USD')

    def test_middleware_uses_safe_defaults_when_settings_cannot_load(self):
        middleware = RuntimeSettingsMiddleware(
            lambda request: JsonResponse({'ok': True})
        )
        with patch(
            'systemsetting.middleware.get_runtime_settings',
            side_effect=ConnectionError,
        ), self.assertLogs('systemsetting.middleware', level='ERROR'):
            response = middleware(RequestFactory().get('/'))
        self.assertEqual(response.status_code, 200)

    def test_overview_hides_system_settings_from_staff(self):
        config = SystemSettings.get_config()
        config.business_name = 'Private business'
        config.tin = 'private-tax-id'
        config.save()
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/settings/')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('system', response.data)
        self.assertNotIn('Private business', str(response.data))
        self.assertNotIn('private-tax-id', str(response.data))
        self.assertIn('notifications', response.data)
        self.assertFalse(response.data['permissions']['can_manage_settings'])

    def test_overview_bootstraps_both_setting_groups_for_admin(self):
        config = SystemSettings.get_config()
        config.business_name = 'Private business'
        config.tin = 'private-tax-id'
        config.save()
        self.client.force_authenticate(user=self.admin)
        response = self.client.get('/settings/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['system']['business_name'], 'Private business')
        self.assertEqual(response.data['system']['tin'], 'private-tax-id')
        self.assertIn('notifications', response.data)
        self.assertTrue(response.data['permissions']['can_manage_settings'])

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


# Inventory settings and threshold validation
# ----------------------------------------------------------------------------

class InventorySettingsTests(TestCase):
    url = '/settings/inventory/'

    def setUp(self):
        self.client = APIClient()
        self.admin = make_user('inventorysettingsadmin', role='admin')
        self.client.force_authenticate(self.admin)

    def test_defaults_and_settings_overview(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['low_stock_threshold'], 20)
        self.assertEqual(response.data['near_expiry_threshold'], 7)
        self.assertEqual(self.client.get('/settings/').data['inventory'], response.data)

    def test_updates_share_existing_inventory_configuration(self):
        for method in ('put', 'patch'):
            response = getattr(self.client, method)(self.url, {
                'low_stock_threshold': 25,
                'near_expiry_threshold': 10,
                'critical_expiry_threshold': 2,
            }, format='json')
            self.assertEqual(response.status_code, 200)
        config = FEFOConf.get_config()
        self.assertEqual(config.low_stock_threshold, 25)
        self.assertEqual(config.near_expiry_threshold, 10)
        self.assertEqual(FEFOConf.objects.count(), 1)
        self.assertEqual(
            self.client.get('/inventory/fefo-config/1/').data,
            self.client.get(self.url).data,
        )

    def test_permissions(self):
        for user, expected in ((None, 401), (make_user('inventorystaff'), 403)):
            self.client.force_authenticate(user)
            for method in ('get', 'put', 'patch'):
                with self.subTest(user=user, method=method):
                    response = getattr(self.client, method)(
                        self.url, {'low_stock_threshold': 99}, format='json',
                    )
                    self.assertEqual(response.status_code, expected)
        self.assertFalse(FEFOConf.objects.exists())

    def test_invalid_thresholds_do_not_change_configuration(self):
        for payload in (
            {'low_stock_threshold': -1},
            {'near_expiry_threshold': -1},
            {'near_expiry_threshold': 2},
            {'low_stock_threshold': 1.5},
            {'near_expiry_threshold': None},
        ):
            with self.subTest(payload=payload):
                response = self.client.patch(self.url, payload, format='json')
                self.assertEqual(response.status_code, 400)
        config = FEFOConf.get_config()
        self.assertEqual(config.low_stock_threshold, 20)
        self.assertEqual(config.near_expiry_threshold, 7)

    def test_updated_thresholds_control_product_and_ingredient_alerts(self):
        product = Product.objects.create(
            category=Category.objects.create(name='Alerts'), name='Milk',
            unit='liter',  shelf_life=30,
        )
        ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=10, shelf_life=30,
        )
        for model, relation in (
            (ProductBatch, {'product': product}),
            (IngredientBatch, {'ingredient': ingredient}),
        ):
            model.objects.create(
                **relation, batch_number='ALERT-BATCH', unit_price=10,
                initial_quantity=Decimal('18'), remaining_quantity=Decimal('18'),
                expiration_date=timezone.localdate() + timedelta(days=8),
            )
        for stock_check in (BatchService.check_product_stock, BatchService.check_ingredient_stock):
            self.assertEqual(len(stock_check()), 1)
        for expiry_check in (BatchService.check_product_expiration, BatchService.check_ingredient_expiration):
            self.assertFalse(expiry_check().exists())

        response = self.client.patch(self.url, {
            'low_stock_threshold': 17, 'near_expiry_threshold': 8,
        }, format='json')
        self.assertEqual(response.status_code, 200)
        for stock_check in (BatchService.check_product_stock, BatchService.check_ingredient_stock):
            self.assertEqual(stock_check(), [])
        for expiry_check in (BatchService.check_product_expiration, BatchService.check_ingredient_expiration):
            self.assertEqual(expiry_check().count(), 1)

        for url, item in (('/inventory/products/', product), ('/inventory/ingredients/', ingredient)):
            self.assertEqual(self.client.get(f'{url}{item.pk}/').data['low_stock_threshold'], 17)
            for value in (0, 19, None):
                with self.subTest(url=url, value=value):
                    response = self.client.patch(
                        f'{url}{item.pk}/', {'low_stock_threshold': value}, format='json',
                    )
                    self.assertEqual(response.status_code, 400)
                    self.assertIn('low_stock_threshold', response.data)

        # Zero is supported globally and applies to every item.
        response = self.client.patch(self.url, {'low_stock_threshold': 0}, format='json')
        self.assertEqual(response.status_code, 200)
        for stock_check in (BatchService.check_product_stock, BatchService.check_ingredient_stock):
            self.assertEqual(stock_check(), [])

    def test_item_creation_rejects_threshold_overrides(self):
        category = Category.objects.create(name='Creation')
        for url, payload in (
            ('/inventory/products/', {
                'category_id': category.pk, 'name': 'Milk', 'unit': 'liter',
                'shelf_life': 7,
            }),
            ('/inventory/ingredients/', {
                'name': 'Raw Milk', 'unit': 'liter', 'unit_price': 10, 'shelf_life': 7,
            }),
        ):
            for value in (0, 10, None):
                response = self.client.post(url, {**payload, 'low_stock_threshold': value}, format='json')
                self.assertEqual(response.status_code, 400)
            response = self.client.post(url, payload, format='json')
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.data['low_stock_threshold'], 20)


# Persistent notification inbox
# ----------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class NotificationInboxTests(TestCase):
    def setUp(self):
        self.admin = Users.objects.create_user(username='alert-admin', email='alert-admin@example.test', role='admin', password='secret123!')
        self.staff = Users.objects.create_user(username='alert-staff', email='alert-staff@example.test', role='staff', password='secret123!')
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(name='Milk', category=self.category, unit='liter',  shelf_life=7)
        self.batch = ProductBatch.objects.create(product=self.product, batch_number='ALERT-001', initial_quantity=5,
            remaining_quantity=5, expiration_date=timezone.localdate()+timedelta(days=2))
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def feed(self):
        response=self.client.get('/settings/inbox/')
        self.assertEqual(response.status_code,200)
        return response.data

    def act(self, action, rows):
        return self.client.post('/settings/inbox/', {'action':action,'notifications':[
            {'id':r['id'],'revision':r['revision']} for r in rows]}, format='json')

    def test_read_persists_across_requests_and_is_per_user(self):
        row=self.feed()[0]
        self.assertTrue(row['unread'])
        self.assertEqual(self.act('read',[row]).status_code,200)
        self.assertFalse(self.feed()[0]['unread'])
        first=NotificationState.objects.get().read_at
        self.assertEqual(self.act('read',[row]).status_code,200)
        self.assertEqual(NotificationState.objects.get().read_at,first)
        other=APIClient();other.force_authenticate(self.admin)
        self.assertFalse(other.get('/settings/inbox/').data[0]['unread'])
        self.client.force_authenticate(self.staff)
        self.assertTrue(self.feed()[0]['unread'])
        self.assertEqual(NotificationState.objects.count(),1)

    def test_dismiss_restore_and_bulk_read_do_not_modify_inventory(self):
        rows=self.feed()
        self.assertGreater(len(rows),1)
        self.assertEqual(self.act('dismiss',[rows[0]]).status_code,200)
        self.assertTrue(self.feed()[0]['dismissed'])
        self.assertFalse(self.feed()[0]['unread'])
        self.assertEqual(self.act('restore',[rows[0]]).status_code,200)
        self.assertFalse(self.feed()[0]['dismissed'])
        self.assertFalse(self.feed()[0]['unread'])
        self.assertEqual(self.act('read',rows).status_code,200)
        self.assertTrue(all(not r['unread'] for r in self.feed()))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity,Decimal('5'))

    def test_changed_stock_reappears_and_stale_action_is_rejected(self):
        row=self.feed()[0]
        self.act('dismiss',[row])
        self.batch.remaining_quantity=4;self.batch.save()
        changed=self.feed()[0]
        self.assertNotEqual(changed['revision'],row['revision'])
        self.assertTrue(changed['unread'])
        self.assertFalse(changed['dismissed'])
        self.assertEqual(self.act('read',[row]).status_code,409)
        self.assertEqual(NotificationState.objects.count(),1)

    def test_validation_is_atomic_and_ignores_spoofed_user_scope(self):
        row=self.feed()[0]
        response=self.act('read',[row,{'id':'nonexistent','revision':'f'*64}])
        self.assertEqual(response.status_code,409)
        self.assertFalse(NotificationState.objects.exists())
        response=self.client.post('/settings/inbox/',{'action':'read','user_id':self.staff.pk,'notifications':[{'id':row['id'],'revision':row['revision']}]},format='json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(NotificationState.objects.get().user,self.admin)
        for data in ({'action':'erase','notifications':[row]}, {'action':'read','notifications':[]}, {'action':'read','notifications':[{'id':row['id'],'revision':'bad'}]}):
            self.assertEqual(self.client.post('/settings/inbox/',data,format='json').status_code,400)

    def test_disabled_alerts_and_role_visibility_remain_authoritative(self):
        self.category.is_visible_to_staff=False;self.category.save()
        admin_rows=self.feed()
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.feed(),[])
        self.assertEqual(self.act('read',admin_rows).status_code,409)
        self.client.force_authenticate(self.admin)
        settings=NotificationSettings.get_config()
        settings.low_stock_alerts=False;settings.near_expiry_alerts=False;settings.new_order_alerts=False;settings.save()
        self.assertEqual(self.feed(),[])

    def test_inactive_category_does_not_raise_staff_stock_alerts(self):
        self.category.is_active = False
        self.category.save(update_fields=['is_active'])
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.feed(), [])

    def test_staff_does_not_receive_order_alert_for_inactive_category(self):
        customer = Customer.objects.create(name='Alert Customer', created_by=self.admin)
        order = SalesService.place_order(
            customer, [(self.product, Decimal('1.00'), Decimal('50.00'))], self.admin,
            amount_tendered=Decimal('50.00'),
        )
        self.category.is_active = False
        self.category.save(update_fields=['is_active'])
        self.client.force_authenticate(self.staff)
        self.assertNotIn(f'fulfilled-{order.pk}', [item['id'] for item in self.feed()])

    def test_unauthenticated_requests_are_rejected(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/settings/inbox/').status_code,401)
        self.assertEqual(self.client.post('/settings/inbox/',{},format='json').status_code,401)

    def test_expired_alerts_bulk_read_survives_a_fresh_client(self):
        self.batch.expiration_date = timezone.localdate() - timedelta(days=1)
        self.batch.save(update_fields=['expiration_date'])
        ingredient = Ingredient.objects.create(name='Cream', unit='liter', unit_price=20, shelf_life=7)
        batch = IngredientBatch.objects.create(
            ingredient=ingredient, batch_number='EXPIRED-CREAM', initial_quantity=3,
            remaining_quantity=3, expiration_date=self.batch.expiration_date, status='expired',
        )
        expected = {f'expiry-product-{self.batch.pk}', f'expiry-ingredient-{batch.pk}'}
        rows = self.feed()
        self.assertEqual({row['id'] for row in rows if row['id'].startswith('expiry-')}, expected)
        self.assertEqual(self.act('read', rows).status_code, 200)
        self.batch.status = 'expired'
        self.batch.save(update_fields=['status'])
        fresh = APIClient()
        fresh.force_authenticate(self.admin)
        refreshed = fresh.get('/settings/inbox/').data
        self.assertTrue(all(not row['unread'] for row in refreshed))
        self.assertEqual({row['id']: row['revision'] for row in rows},
                         {row['id']: row['revision'] for row in refreshed})
        self.client.force_authenticate(self.staff)
        self.assertTrue(all(row['unread'] for row in self.feed()))
        settings = NotificationSettings.get_config()
        settings.near_expiry_alerts = False
        settings.save(update_fields=['near_expiry_alerts'])
        self.assertFalse(any(row['id'].startswith('expiry-') for row in self.feed()))

    def test_expired_alert_dismiss_restore_and_inventory_filters(self):
        self.batch.expiration_date = timezone.localdate() - timedelta(days=1)
        self.batch.status = 'expired'
        self.batch.save(update_fields=['expiration_date', 'status'])
        key = f'expiry-product-{self.batch.pk}'
        row = next(row for row in self.feed() if row['id'] == key)
        self.assertEqual(self.act('dismiss', [row]).status_code, 200)
        refreshed = next(row for row in self.feed() if row['id'] == key)
        self.assertTrue(refreshed['dismissed'])
        self.assertFalse(refreshed['unread'])
        self.assertEqual(self.act('restore', [row]).status_code, 200)
        self.assertFalse(next(row for row in self.feed() if row['id'] == key)['dismissed'])
        self.category.is_visible_to_staff = False
        self.category.save(update_fields=['is_visible_to_staff'])
        self.client.force_authenticate(self.staff)
        self.assertNotIn(key, [row['id'] for row in self.feed()])
        self.client.force_authenticate(self.admin)
        for status, quantity in [('disposed', 5), ('depleted', 5), ('available', 0)]:
            self.batch.status = status
            self.batch.remaining_quantity = quantity
            self.batch.save(update_fields=['status', 'remaining_quantity'])
            self.assertNotIn(key, [row['id'] for row in self.feed()])

    def test_newly_expired_batch_has_a_new_revision(self):
        row = next(row for row in self.feed() if row['id'] == f'expiry-product-{self.batch.pk}')
        self.assertEqual(self.act('read', [row]).status_code, 200)
        with patch('django.utils.timezone.localdate', return_value=self.batch.expiration_date + timedelta(days=1)):
            expired = next(item for item in self.feed() if item['id'] == row['id'])
            self.assertNotEqual(expired['revision'], row['revision'])
            self.assertTrue(expired['unread'])
            self.assertEqual(self.act('read', [row]).status_code, 409)
