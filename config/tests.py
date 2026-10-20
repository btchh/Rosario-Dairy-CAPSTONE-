"""Cross-app regression, shared cache, authentication and security tests.

Run all app tests: python manage.py test config
Run one class: python manage.py test config.tests.<TestClass>
PostgreSQL is required for the concurrency and database-locking checks.
"""

import re
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.db import transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import EmailChangeChallenge, PasswordResetChallenge
from accounts.services import auth_service, password_reset_service
from accounts.services.email_change_service import confirm_email_change
from config.api_inputs import require_item_list
from config.data_cache import data_version
from inventory.models import Category, Product, ProductBatch
from reporting.services import (
    daily_sales,
    get_report,
    monthly_sales,
    report_refresh_slot,
    weekly_sales,
)
from sales.models import Customer, Transaction, TransactionItem


User = get_user_model()


# Cross-app regressions
# ----------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BackendRegressionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username='regression-admin', email='regression@example.com',
            password='Original-password-493!', role='admin',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.category = Category.objects.create(name='Dairy')
        self.product = Product.objects.create(
            name='Original milk', category=self.category, unit='liter',
            shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='REGRESSION-001',
            initial_quantity=10, remaining_quantity=10, unit_price=50,
            expiration_date=timezone.localdate() + timedelta(days=7),
        )

    def sale(self):
        response = self.client.post('/sales/checkout/', {
            'items': [{'product_id': self.product.pk, 'quantity': '2.00', 'unit_price': '50', 'discount_type': 'percent', 'discount_value': '10.00'}],
            'payment_method': 'cash', 'amount_tendered': '100.00',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return Transaction.objects.get(pk=response.data['id'])

    def test_weekly_breakdown_reconciles_discounted_transaction_totals(self):
        self.sale()
        report = weekly_sales()
        self.assertEqual(report['revenue'], Decimal('90.00'))
        self.assertEqual(sum(day['revenue'] for day in report['daily_breakdown']), report['revenue'])

    def test_product_names_remain_at_sale_time_after_catalog_rename(self):
        self.sale()
        self.product.name = 'Renamed milk'
        self.product.save()
        self.assertEqual(daily_sales()['items'][0]['product_name'], 'Original milk')
        self.assertEqual(weekly_sales()['top_products'][0]['product_name'], 'Original milk')
        self.assertEqual(monthly_sales()['top_products'][0]['product_name'], 'Original milk')

    def test_pdf_treats_product_names_as_text(self):
        self.product.name = 'Milk <b> & cream'
        self.product.save()
        self.sale()
        response = self.client.get('/api/reports/export-pdf/?type=daily_sales')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))

    def test_reports_accept_supported_large_transaction_values(self):
        Transaction.objects.create(handled_by=self.user, subtotal=Decimal('2000000000000.00'),
                                   total_amount=Decimal('2000000000000.00'))
        for kind, field in [('daily_sales', 'total_revenue'), ('weekly_sales', 'revenue'),
                            ('monthly_sales', 'revenue')]:
            with self.subTest(kind=kind):
                response = self.client.get(f'/api/reports/preview/?type={kind}')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Decimal(response.data['data'][field]), Decimal('2000000000000.00'))

    def test_invalid_timezone_paths_return_validation_errors(self):
        for value in ['/etc/passwd', '../UTC', 'Asia//Manila']:
            with self.subTest(value=value):
                response = self.client.patch('/settings/system/', {'timezone': value}, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertIn('timezone', response.data)

    def test_password_reset_does_not_overwrite_newer_account_changes(self):
        get_user_model().objects.filter(pk=self.user.pk).update(
            role='staff', is_active=False, deactivation_reason='suspended', first_name='Updated',
        )
        auth_service.forgot_password(self.user, 'Replacement-password-594!')
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, 'staff')
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.user.first_name, 'Updated')
        self.assertTrue(self.user.check_password('Replacement-password-594!'))

    def test_password_change_rejects_a_stale_old_password(self):
        fresh = get_user_model().objects.get(pk=self.user.pk)
        fresh.set_password('New-admin-password-684!')
        fresh.save(update_fields=['password'])
        with self.assertRaises(ValueError):
            auth_service.change_password(self.user, 'Original-password-493!', 'Replacement-password-594!')
        fresh.refresh_from_db()
        self.assertTrue(fresh.check_password('New-admin-password-684!'))

    def test_password_change_checks_current_cooldown(self):
        get_user_model().objects.filter(pk=self.user.pk).update(last_password_change_at=timezone.now())
        with self.assertRaises(ValueError):
            auth_service.change_password(self.user, 'Original-password-493!', 'Replacement-password-594!')

    def test_non_string_passwords_return_validation_errors(self):
        for endpoint, payload in [
            ('change-password', {'old_password': 'Original-password-493!', 'new_password': 12345678}),
            ('admin-reset-password', {'username': self.user.username, 'new_password': ['invalid']}),
            ('register', {'username': 'new-user', 'email': 'new@example.com', 'role': 'staff', 'password': 12345678}),
        ]:
            with self.subTest(endpoint=endpoint):
                response = self.client.post(f'/accounts/{endpoint}/', payload, format='json')
                self.assertEqual(response.status_code, 400)

    def test_manual_endpoints_reject_non_object_bodies_without_mutations(self):
        endpoints = [
            ('post', '/sales/checkout/'),
            ('post', '/inventory/stock-counts/'),
            ('post', '/inventory/stock-adjustments/'),
            ('post', '/accounts/register/'),
            ('post', '/accounts/change-password/'),
            ('post', '/accounts/admin-reset-password/'),
            ('post', '/accounts/forgot-password/'),
            ('post', '/accounts/reset-password/'),
            ('post', '/accounts/logout/'),
            ('patch', '/accounts/user/'),
            ('patch', f'/accounts/users/{self.user.pk}/'),
            ('delete', f'/accounts/users/{self.user.pk}/'),
        ]
        for method, endpoint in endpoints:
            with self.subTest(endpoint=endpoint, method=method):
                response = getattr(self.client, method)(endpoint, ['invalid'], format='json')
                self.assertEqual(response.status_code, 400)
        self.batch.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.batch.remaining_quantity, Decimal('10.00'))
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertTrue(self.user.is_active)

    def test_otp_rechecks_account_after_initial_lookup(self):
        identity = password_reset_service._identity(self.user.username, self.user.email)
        PasswordResetChallenge.objects.create(
            user=self.user, otp_digest=password_reset_service._otp_digest(identity, '123456'),
            expires_at=timezone.now() + timedelta(minutes=10), last_sent_at=timezone.now(),
        )
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        with patch('accounts.services.password_reset_service._matching_active_user', return_value=self.user):
            accepted = password_reset_service.reset_password_with_otp(
                self.user.username, self.user.email, '123456', 'Replacement-password-594!',
            )
        self.assertFalse(accepted)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertTrue(self.user.check_password('Original-password-493!'))

    def test_product_breakdown_explicitly_identifies_gross_sales(self):
        self.sale()
        for kind in ('daily_sales', 'weekly_sales', 'monthly_sales'):
            with self.subTest(kind=kind):
                response = self.client.get(f'/api/reports/preview/?type={kind}')
                self.assertEqual(response.data['data']['product_revenue_basis'], 'gross_before_discounts')


# Shared data caches
# ----------------------------------------------------------------------------

class DataCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(
            username='cache-admin', email='cache-admin@example.com', role='admin',
        )
        self.staff = get_user_model().objects.create_user(
            username='cache-staff', email='cache-staff@example.com', role='staff',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.category = Category.objects.create(name='Visible')
        self.product = Product.objects.create(
            name='Milk', category=self.category, unit='L',  shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='CACHE-001', initial_quantity=10,
            remaining_quantity=10, expiration_date=timezone.localdate() + timedelta(days=7),
        )

    def test_repeated_lists_and_dashboard_aggregates_avoid_data_queries(self):
        endpoints = [
            '/inventory/products/', '/inventory/ingredients/',
            '/inventory/product-batches/', '/inventory/ingredient-batches/',
            '/inventory/categories/', '/inventory/low-stock/products/',
            '/sales/transactions/', '/sales/orders/', '/sales/customers/',
            '/sales/reports/revenue/', '/sales/reports/best-sellers/',
            '/sales/reports/sales-by-category/', '/api/forecasting/forecast/',
        ]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                first = self.client.get(endpoint)
                self.assertEqual(first.status_code, 200, first.data)
                # Some handlers create singleton configuration on first use.
                self.client.get(endpoint)
                with self.assertNumQueries(0):
                    repeated = self.client.get(endpoint)
                self.assertEqual(first.data, repeated.data)

    def test_commit_invalidates_stock_and_report_payloads(self):
        endpoint = '/inventory/products/'
        first = self.client.get(endpoint)
        self.assertEqual(Decimal(first.data[0]['total_stock']), 10)
        get_report('inventory')
        with self.captureOnCommitCallbacks(execute=True):
            self.batch.remaining_quantity = 3
            self.batch.save()
        refreshed = self.client.get(endpoint)
        self.assertEqual(Decimal(refreshed.data[0]['total_stock']), 3)
        self.assertEqual(get_report('inventory')['items'][0]['quantity'], Decimal('3'))

    def test_rollback_does_not_invalidate(self):
        version = data_version()
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    self.product.name = 'Rolled back'
                    self.product.save()
                    raise ValueError('rollback')
            except ValueError:
                pass
        self.assertEqual(data_version(), version)

    def test_staff_scope_and_permissions_are_checked_on_warm_cache(self):
        self.category.is_visible_to_staff = False
        self.category.save()
        admin = self.client.get('/inventory/products/')
        self.assertEqual(len(admin.data), 1)
        self.client.force_authenticate(self.staff)
        staff = self.client.get('/inventory/products/')
        self.assertEqual(staff.data, [])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/inventory/products/').status_code, 401)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get('/sales/reports/revenue/').status_code, 403)

    def test_filters_and_pages_have_separate_keys(self):
        for amount in (10, 20):
            Transaction.objects.create(handled_by=self.admin, subtotal=amount, total_amount=amount)
        first = self.client.get('/sales/transactions/?page_size=1')
        self.assertEqual(first.status_code, 200)
        second = self.client.get('/sales/transactions/?page_size=1&page=2')
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(first.data['results'][0]['id'], second.data['results'][0]['id'])
        self.assertEqual(self.client.get('/sales/transactions/?payment_method=invalid').status_code, 400)
        self.assertEqual(self.client.get('/sales/transactions/?page_size=1&page=3').status_code, 404)

    def test_deletion_invalidates_cached_catalog(self):
        self.client.get('/inventory/products/')
        with self.captureOnCommitCallbacks(execute=True):
            self.batch.delete()
            self.product.delete()
        self.assertEqual(self.client.get('/inventory/products/').data, [])

    def test_category_visibility_change_invalidates_staff_cache(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(len(self.client.get('/inventory/products/').data), 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.category.is_visible_to_staff = False
            self.category.save()
        self.assertEqual(self.client.get('/inventory/products/').data, [])

    def test_checkout_invalidates_history_and_stock_after_commit(self):
        self.assertEqual(self.client.get('/sales/transactions/').data['count'], 0)
        self.client.get('/inventory/products/')
        with self.captureOnCommitCallbacks(execute=True):
            sale = self.client.post('/sales/checkout/', {
                'items': [{'product_id': self.product.pk, 'quantity': '2.00', 'unit_price': '50.00'}],
                'payment_method': 'cash', 'amount_tendered': '100.00',
            }, format='json')
        self.assertEqual(sale.status_code, 201, sale.data)
        self.assertEqual(self.client.get('/sales/transactions/').data['count'], 1)
        self.assertEqual(Decimal(self.client.get('/inventory/products/').data[0]['total_stock']), 8)

    def test_cache_failure_still_returns_database_data(self):
        with patch('config.data_cache.cache.get', side_effect=ConnectionError), \
             patch('config.data_cache.cache.set', side_effect=ConnectionError):
            response = self.client.get('/inventory/products/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['name'], 'Milk')

    def test_forecast_cache_separates_periods_and_supports_force_refresh(self):
        with patch('reporting.services.sarima_forecast', side_effect=lambda period: {'period': period}) as build:
            self.assertEqual(get_report('sarima_forecast', period='weekly'), {'period': 'weekly'})
            get_report('sarima_forecast', period='weekly')
            get_report('sarima_forecast', period='monthly')
            self.assertEqual(build.call_count, 2)
            get_report('sarima_forecast', force_refresh=True, period='weekly')
            self.assertEqual(build.call_count, 3)

    def test_date_rollover_does_not_reuse_yesterdays_payload(self):
        with patch('config.data_cache.timezone.localdate', return_value=timezone.localdate() - timedelta(days=1)):
            self.client.get('/inventory/products/')
        with patch('inventory.views.product_views.ProductViewSet.get_queryset', side_effect=RuntimeError('new read')):
            with self.assertRaisesMessage(RuntimeError, 'new read'):
                self.client.get('/inventory/products/')

    def test_manual_report_refresh_invalidates_api_cache(self):
        self.client.get('/inventory/products/')
        old_version = data_version()
        with self.captureOnCommitCallbacks(execute=True), \
             patch('reporting.views.refresh_reports', return_value=(timezone.now(), {})):
            response = self.client.post('/api/reports/refresh/')
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(data_version(), old_version)


# Account security and concurrent requests
# ----------------------------------------------------------------------------

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
        product = Product.objects.create(name=name, category=category, unit='piece', shelf_life=7)
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
        response = self.client.post('/sales/checkout/', {'items':[{'product_id':product.pk,'quantity':'1.00', 'unit_price': '100'}], 'amount_tendered':'100.00'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Transaction.objects.get().total_amount, 100)

    def test_staff_new_batch_does_not_default_to_a_selling_price(self):
        product, _ = self.product()
        values = {'product_id':product.pk, 'quantity':'5.00', 'expiration_date':str(timezone.localdate()+timedelta(days=7))}
        response = self.client.post('/inventory/product-batches/', {**values, 'unit_price':'0.00'}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/inventory/product-batches/', values, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(ProductBatch.objects.get(pk=response.data['id']).unit_price)

    def test_staff_cannot_use_discounts_as_a_price_override(self):
        product, batch = self.product()
        customer = Customer.objects.create(name='Buyer', created_by=self.user)
        for url in ('/sales/checkout/', '/sales/orders/'):
            response = self.client.post(url, {
                'customer_id': customer.pk,
                'items': [{'product_id': product.pk, 'quantity': '1.00', 'unit_price': '100', 'discount_type': 'percent', 'discount_value': '100.00'}],
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
