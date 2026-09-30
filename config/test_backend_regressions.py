from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import PasswordResetChallenge
from accounts.services import auth_service, password_reset_service
from inventory.models import Category, Product, ProductBatch
from reporting.services import daily_sales, weekly_sales, monthly_sales
from sales.models import Transaction


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
            unit_price=Decimal('50.00'), shelf_life=7,
        )
        self.batch = ProductBatch.objects.create(
            product=self.product, batch_number='REGRESSION-001',
            initial_quantity=10, remaining_quantity=10, unit_price=50,
            expiration_date=timezone.localdate() + timedelta(days=7),
        )

    def sale(self):
        response = self.client.post('/sales/checkout/', {
            'items': [{'product_id': self.product.pk, 'quantity': '2.00'}],
            'payment_method': 'cash', 'amount_tendered': '100.00',
            'discount_type': 'percent', 'discount_value': '10.00',
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
