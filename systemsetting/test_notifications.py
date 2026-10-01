from datetime import timedelta
from decimal import Decimal
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from accounts.models import Users
from inventory.models import Category, Product, ProductBatch
from systemsetting.models import NotificationSettings, NotificationState


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class NotificationInboxTests(TestCase):
    def setUp(self):
        self.admin = Users.objects.create_user(username='alert-admin', email='alert-admin@example.test', role='admin', password='secret123!')
        self.staff = Users.objects.create_user(username='alert-staff', email='alert-staff@example.test', role='staff', password='secret123!')
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(name='Milk', category=self.category, unit='liter', unit_price=50, shelf_life=7, low_stock_threshold=10)
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

    def test_unauthenticated_requests_are_rejected(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/settings/inbox/').status_code,401)
        self.assertEqual(self.client.post('/settings/inbox/',{},format='json').status_code,401)
