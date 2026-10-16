from datetime import timedelta
from decimal import Decimal
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import EmailChangeChallenge, PasswordResetChallenge
from accounts.services.email_change_service import confirm_email_change
from config.api_inputs import require_item_list
from inventory.models import Category, Product, ProductBatch
from reporting.services import get_report, report_refresh_slot
from sales.models import Customer, Transaction, TransactionItem

User = get_user_model()


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class SecurityHardeningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='securitystaff', email='staff@example.test',
                                             password='InitialAuditPass!97', role='staff')
        self.client = APIClient()
        self.login()

    def login(self):
        response = APIClient().post('/accounts/login/', {
            'username': self.user.username, 'password': 'InitialAuditPass!97',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.tokens = response.data
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.tokens['access'])

    def product(self, name='Milk', hidden=False):
        category = Category.objects.create(name=name, is_visible_to_staff=not hidden)
        product = Product.objects.create(name=name, category=category, unit='piece', unit_price=100, shelf_life=7)
        batch = ProductBatch.objects.create(product=product, batch_number=name, unit_price=100,
            initial_quantity=10, remaining_quantity=10, expiration_date=timezone.localdate()+timedelta(days=7))
        return product, batch

    def email_request(self):
        data = {'email': 'verified@example.test', 'current_password': 'InitialAuditPass!97'}
        response = self.client.post('/accounts/email-change/', data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        otp = re.search(r'code is: (\d{6})', mail.outbox[-1].body).group(1)
        return {**data, 'otp': otp}

    def test_stolen_access_token_cannot_change_recovery_email(self):
        response = self.client.patch('/accounts/user/', {'email': 'attacker@example.test'}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/accounts/email-change/', {'email': 'attacker@example.test'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'staff@example.test')
        self.assertFalse(EmailChangeChallenge.objects.exists())

    def test_email_change_requires_password_and_verified_code_and_revokes_sessions(self):
        PasswordResetChallenge.objects.create(user=self.user, otp_digest='old', expires_at=timezone.now()+timedelta(minutes=10), last_sent_at=timezone.now())
        values = self.email_request()
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'staff@example.test')
        response = self.client.patch('/accounts/email-change/', {**values, 'current_password': 'wrong'}, format='json')
        self.assertEqual(response.status_code, 400)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch('/accounts/email-change/', values, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'verified@example.test')
        self.assertFalse(PasswordResetChallenge.objects.exists())
        self.assertFalse(EmailChangeChallenge.objects.exists())
        self.assertEqual(mail.outbox[-1].to, ['staff@example.test'])
        self.assertEqual(self.client.get('/accounts/user/').status_code, 401)
        self.assertEqual(APIClient().post('/accounts/refresh/', {'refresh': self.tokens['refresh']}, format='json').status_code, 401)
        self.assertFalse(confirm_email_change(self.user, values['current_password'], 'another@example.test', values['otp']))

    def test_email_code_attempt_limit_is_persisted(self):
        values = self.email_request()
        wrong = '000000' if values['otp'] != '000000' else '999999'
        for _ in range(5):
            self.assertFalse(confirm_email_change(self.user, values['current_password'], values['email'], wrong))
        self.assertFalse(EmailChangeChallenge.objects.exists())
        self.assertFalse(confirm_email_change(self.user, values['current_password'], values['email'], values['otp']))

    def test_expired_code_cannot_change_email(self):
        values = self.email_request()
        EmailChangeChallenge.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertFalse(confirm_email_change(self.user, values['current_password'], values['email'], values['otp']))
        self.assertFalse(EmailChangeChallenge.objects.exists())

    def test_admin_cannot_bypass_own_email_verification_via_user_editor(self):
        self.user.role = 'admin'
        self.user.save(update_fields=['role'])
        response = self.client.patch(f'/accounts/users/{self.user.pk}/', {'email':'attacker@example.test'}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_username_change_needs_current_password(self):
        response = self.client.patch('/accounts/user/', {'username': 'newname'}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.patch('/accounts/user/', {'username': 'newname', 'current_password': 'InitialAuditPass!97'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_staff_cannot_override_existing_batch_price(self):
        product, batch = self.product()
        for value in ('0.00', '0.01', None):
            response = self.client.patch(f'/inventory/product-batches/{batch.pk}/', {'unit_price': value}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        batch.refresh_from_db()
        self.assertEqual(batch.unit_price, 100)
        response = self.client.post('/sales/checkout/', {'items':[{'product_id':product.pk,'quantity':'1.00'}], 'amount_tendered':'100.00'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Transaction.objects.get().total_amount, 100)

    def test_staff_new_batch_uses_server_price(self):
        product, _ = self.product()
        values = {'product_id':product.pk, 'quantity':'5.00', 'expiration_date':str(timezone.localdate()+timedelta(days=7))}
        response = self.client.post('/inventory/product-batches/', {**values, 'unit_price':'0.00'}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/inventory/product-batches/', values, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(ProductBatch.objects.get(pk=response.data['id']).unit_price, 100)

    def test_staff_cannot_use_discounts_as_a_price_override(self):
        product, batch = self.product()
        customer = Customer.objects.create(name='Buyer', created_by=self.user)
        for url in ('/sales/checkout/', '/sales/orders/'):
            response = self.client.post(url, {
                'customer_id': customer.pk,
                'items': [{'product_id': product.pk, 'quantity': '1.00'}],
                'discount_type': 'percent', 'discount_value': '100.00',
                'amount_tendered': '0.00',
            }, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Transaction.objects.exists())
        batch.refresh_from_db()
        self.assertEqual(batch.remaining_quantity, 10)

    def test_admin_can_still_set_batch_price(self):
        _, batch = self.product()
        self.user.role='admin'
        self.user.save(update_fields=['role'])
        response = self.client.patch(f'/inventory/product-batches/{batch.pk}/', {'unit_price':'85.00'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_sales_reports_exclude_hidden_and_mixed_transactions_from_all_totals(self):
        visible, visible_batch = self.product('Visible')
        _, hidden_batch = self.product('Hidden', hidden=True)
        customer = Customer.objects.create(name='Restricted customer', created_by=self.user)
        for batches in ([hidden_batch], [visible_batch, hidden_batch]):
            txn = Transaction.objects.create(handled_by=self.user, customer=customer, subtotal=200, total_amount=200)
            for batch in batches:
                TransactionItem.objects.create(transaction=txn,product_batch=batch,quantity=1,unit_price=100,
                    product_name_snapshot=batch.product.name,category_name_snapshot=batch.product.category.name)
        txn = Transaction.objects.create(handled_by=self.user,subtotal=100,total_amount=100)
        TransactionItem.objects.create(transaction=txn,product_batch=visible_batch,quantity=1,unit_price=100)
        for report_type in ('daily_sales','weekly_sales','monthly_sales','customer'):
            with self.subTest(report_type=report_type):
                data = get_report(report_type, visible_to_staff=True)
                self.assertNotIn('Hidden', str(data))
                self.assertNotIn('Restricted customer', str(data))
                if report_type == 'customer':
                    self.assertEqual(data['total_lifetime_value'], 0)
                    self.assertEqual(data['unassigned_revenue'], 100)
                else:
                    self.assertEqual(data['gross_sales'], 100)
                    self.assertEqual(data['transaction_count'], 1)
        self.assertEqual(get_report('daily_sales')['total_revenue'], 500)
        response = self.client.get('/api/reports/export-pdf/?type=daily_sales')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))

    def test_inactive_categories_are_also_excluded(self):
        _, batch = self.product()
        batch.product.category.is_active=False
        batch.product.category.save(update_fields=['is_active'])
        txn=Transaction.objects.create(handled_by=self.user, subtotal=100, total_amount=100)
        TransactionItem.objects.create(transaction=txn,product_batch=batch,quantity=1,unit_price=100)
        self.assertEqual(get_report('daily_sales', visible_to_staff=True)['total_revenue'], 0)

    def test_global_forecasts_require_admin_for_api_preview_and_pdf(self):
        for url in ('/api/forecasting/forecast/', '/api/reports/preview/?type=sarima_forecast', '/api/reports/export-pdf/?type=sarima_forecast'):
            self.assertEqual(self.client.get(url).status_code,403,url)
        self.user.role='admin'
        self.user.save(update_fields=['role'])
        self.assertEqual(self.client.get('/api/forecasting/forecast/').status_code,200)

    def test_forwarded_header_rotation_does_not_bypass_login_throttle(self):
        cache.clear()
        anonymous=APIClient()
        statuses=[anonymous.post('/accounts/login/', {'username':'nonexistent','password':'bad'},format='json',
            HTTP_X_FORWARDED_FOR=f'192.0.2.{i}', REMOTE_ADDR='198.51.100.5').status_code for i in range(12)]
        self.assertEqual(statuses[:10], [401]*10)
        self.assertEqual(statuses[10:], [429]*2)

    def test_refresh_rotates_and_cannot_be_replayed(self):
        anonymous=APIClient()
        response=anonymous.post('/accounts/refresh/', {'refresh':self.tokens['refresh']},format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(anonymous.post('/accounts/refresh/', {'refresh':self.tokens['refresh']},format='json').status_code,401)
        self.assertEqual(anonymous.post('/accounts/refresh/', {'refresh':response.data['refresh']},format='json').status_code,200)

    def test_logout_revokes_all_access_and_refresh_tokens_and_new_login_works(self):
        other_tokens = APIClient().post('/accounts/login/', {'username':self.user.username,'password':'InitialAuditPass!97'},format='json').data
        response=self.client.post('/accounts/logout/', {'refresh_token':self.tokens['refresh']},format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(self.client.get('/accounts/user/').status_code,401)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+other_tokens['access'])
        self.assertEqual(self.client.get('/accounts/user/').status_code,401)
        self.assertEqual(APIClient().post('/accounts/refresh/', {'refresh':other_tokens['refresh']},format='json').status_code,401)
        self.login()
        self.assertEqual(self.client.get('/accounts/user/').status_code,200)

    def test_oversized_cart_is_rejected_before_product_queries(self):
        for url in ('/sales/checkout/', '/sales/orders/'):
            response=self.client.post(url, {'items':[{'product_id':1,'quantity':'1.00'}]*101},format='json')
            self.assertEqual(response.status_code,400,response.data)
        self.assertFalse(Transaction.objects.exists())
        with self.assertRaises(ValueError):
            require_item_list([{'product_id':1},{'product_id':'1'}])

    def test_refresh_endpoint_is_throttled(self):
        anonymous=APIClient()
        for _ in range(30):
            self.assertEqual(anonymous.post('/accounts/refresh/',{'refresh':'invalid'},format='json').status_code,401)
        self.assertEqual(anonymous.post('/accounts/refresh/',{'refresh':'invalid'},format='json').status_code,429)

    def test_report_refresh_has_global_cooldown_across_users(self):
        with report_refresh_slot():
            pass
        from rest_framework.exceptions import Throttled
        with self.assertRaises(Throttled):
            with report_refresh_slot():
                self.fail('A second report rebuild must not start.')

    def test_report_refresh_endpoint_limits_repeated_work(self):
        with patch('reporting.views.refresh_reports', return_value=(timezone.now(), {})) as rebuild:
            first=self.client.post('/api/reports/refresh/')
            self.assertEqual(first.status_code,200,first.data)
            second=self.client.post('/api/reports/refresh/')
            self.assertEqual(second.status_code,429)
            self.assertEqual(rebuild.call_count,1)


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ConcurrentSecurityTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.user=User.objects.create_user(username='concurrent-security',email='concurrent@example.test',password='InitialAuditPass!97')

    def test_only_one_concurrent_refresh_can_rotate_same_token(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections
        tokens=APIClient().post('/accounts/login/',{'username':self.user.username,'password':'InitialAuditPass!97'},format='json').data
        barrier=Barrier(2)

        def refresh():
            try:
                barrier.wait(timeout=10)
                return APIClient().post('/accounts/refresh/',{'refresh':tokens['refresh']},format='json').status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(lambda _:refresh(), range(2)))
        self.assertEqual(sorted(statuses),[200,401])

    def test_report_refresh_lock_blocks_another_connection(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import connections
        from rest_framework.exceptions import Throttled

        def rebuild():
            try:
                try:
                    with report_refresh_slot():
                        return 'unexpected success'
                except Throttled as exc:
                    return str(exc.detail)
            finally:
                connections.close_all()

        with report_refresh_slot():
            with ThreadPoolExecutor(max_workers=1) as pool:
                result=pool.submit(rebuild).result(timeout=10)
        self.assertIn('already running',result)
