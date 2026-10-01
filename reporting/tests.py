from datetime import timedelta
from decimal import Decimal
from unittest.mock import call, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from inventory.models import Category, Ingredient, IngredientBatch, Product, ProductBatch
from sales.models import Customer, Transaction, TransactionItem
from systemsetting.models import SystemSettings


User = get_user_model()


class ReportBrandingTests(TestCase):
    def test_pdf_header_includes_saved_business_profile(self):
        from reporting.pdf import Report

        config = SystemSettings.get_config()
        config.business_name = 'Sample Dairy'
        config.business_address = '123 Test Street'
        config.business_contact = '09123456789'
        config.business_email = 'hello@example.test'
        config.tin = '123-456-789'
        config.business_type = 'Dairy retailer'
        config.save()
        cache.clear()

        report = Report('inventory', {'as_of': '2026-10-01'})
        report.header()
        content = ' '.join(part.text for part in report.story if hasattr(part, 'text'))
        for value in ('SAMPLE DAIRY', 'Dairy retailer', '123 Test Street',
                      '09123456789', 'hello@example.test', '123-456-789'):
            self.assertIn(value, content)


class ReportAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.staff = User.objects.create_user(
            username='reportstaff', password='testpass123!',
            email='reportstaff@example.com', role='staff',
        )
        self.customer = Customer.objects.create(name='Report Customer', created_by=self.staff)
        self.category = Category.objects.create(name='Reports')
        self.product = Product.objects.create(
            category=self.category, name='Milk', unit='liter',
            unit_price=Decimal('50.00'), shelf_life=7, low_stock_threshold=10,
        )
        self.product_batch = ProductBatch.objects.create(
            product=self.product, batch_number='PRD-REPORT-001',
            unit_price=Decimal('50.00'), initial_quantity=Decimal('5.00'),
            remaining_quantity=Decimal('5.00'),
            expiration_date=timezone.localdate() + timedelta(days=3),
        )
        self.ingredient = Ingredient.objects.create(
            name='Raw Milk', unit='liter', unit_price=Decimal('20.00'),
            shelf_life=3, low_stock_threshold=10,
        )
        IngredientBatch.objects.create(
            ingredient=self.ingredient, batch_number='ING-REPORT-001',
            unit_price=Decimal('20.00'), initial_quantity=Decimal('20.00'),
            remaining_quantity=Decimal('20.00'),
            expiration_date=timezone.localdate() + timedelta(days=10),
        )
        self.transaction = Transaction.objects.create(
            handled_by=self.staff, customer=self.customer,
            subtotal=Decimal('100.00'), total_amount=Decimal('100.00'),
        )
        TransactionItem.objects.create(
            transaction=self.transaction, product_batch=self.product_batch,
            quantity=Decimal('2.00'), unit_price=Decimal('50.00'),
        )

    def test_authentication_is_required(self):
        response = self.client.get('/api/reports/preview/?type=daily_sales')
        self.assertEqual(response.status_code, 401)

    def test_all_preview_types_return_valid_payloads(self):
        self.client.force_authenticate(user=self.staff)
        for report_type in (
            'daily_sales', 'weekly_sales', 'monthly_sales', 'inventory',
            'sarima_forecast', 'customer',
        ):
            with self.subTest(report_type=report_type):
                response = self.client.get(f'/api/reports/preview/?type={report_type}')
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['report_type'], report_type)
                self.assertIn('data', response.data)

    def test_invalid_report_type_returns_400(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/api/reports/preview/?type=unknown')
        self.assertEqual(response.status_code, 400)

    def test_daily_sales_includes_aggregated_product_breakdown(self):
        TransactionItem.objects.create(
            transaction=self.transaction, product_batch=self.product_batch,
            quantity=Decimal('1.00'), unit_price=Decimal('50.00'),
        )
        voided = Transaction.objects.create(
            handled_by=self.staff, customer=self.customer, is_voided=True,
            subtotal=Decimal('500.00'), total_amount=Decimal('500.00'),
        )
        TransactionItem.objects.create(
            transaction=voided, product_batch=self.product_batch,
            quantity=Decimal('10.00'), unit_price=Decimal('50.00'),
        )
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/api/reports/preview/?type=daily_sales')
        data = response.data['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['total_revenue'], '100.00')
        self.assertEqual(data['transaction_count'], 1)
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(data['items'][0]['product_name'], 'Milk')
        self.assertEqual(data['items'][0]['quantity'], '3.00')
        self.assertEqual(data['items'][0]['total_revenue'], '150.00')
        self.assertNotIn('date', data['items'][0])
        self.assertEqual(data['gross_sales'], '100.00')
        self.assertEqual(data['average_ticket'], '100.00')
        self.assertEqual(data['payment_mix'][0]['transaction_count'], 1)
        self.assertEqual(data['category_mix'][0]['category'], 'Reports')
        self.assertEqual(data['family_mix'][0]['product_name'], 'Milk')
        self.assertEqual(data['family_mix'][0]['quantity'], '3.00')

    def test_daily_sales_pdf_contains_product_breakdown(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/api/reports/export-pdf/?type=daily_sales')
        content = b''.join(response.streaming_content)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(content.startswith(b'%PDF'))
        self.assertGreater(len(content), 2000)

    def test_product_family_rolls_up_variants_and_uses_sale_name_snapshot(self):
        smaller = Product.objects.create(
            category=self.category, name='Milk', variant='500ml', unit='piece',
            unit_price=Decimal('25.00'), shelf_life=7,
        )
        smaller_batch = ProductBatch.objects.create(
            product=smaller, batch_number='PRD-REPORT-500ML',
            unit_price=Decimal('25.00'), initial_quantity=Decimal('5.00'),
            remaining_quantity=Decimal('5.00'),
            expiration_date=timezone.localdate() + timedelta(days=3),
        )
        TransactionItem.objects.create(
            transaction=self.transaction, product_batch=smaller_batch,
            quantity=Decimal('2.00'), unit_price=Decimal('25.00'),
            product_name_snapshot='Milk', product_variant_snapshot='500ml',
        )
        self.client.force_authenticate(user=self.staff)
        data = self.client.get('/api/reports/preview/?type=daily_sales').data['data']

        self.assertEqual(len(data['items']), 2)
        self.assertEqual(data['family_mix'][0], {
            'product_name': 'Milk', 'quantity': '4.00', 'gross_sales': '150.00',
        })

    def test_weekly_sales_includes_complete_daily_breakdown_and_top_products(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/api/reports/preview/?type=weekly_sales')
        data = response.data['data']

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(data['daily_breakdown']), 7)
        self.assertEqual(data['daily_breakdown'][-1]['date'], str(timezone.localdate()))
        self.assertEqual(
            sum(day['transaction_count'] for day in data['daily_breakdown']), 1
        )
        self.assertEqual(data['top_products'][0]['product_name'], 'Milk')
        self.assertEqual(data['top_products'][0]['quantity'], '2.00')
        self.assertEqual(data['top_products'][0]['revenue'], '100.00')

    def test_monthly_sales_uses_weekly_breakdown(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/api/reports/preview/?type=monthly_sales')
        data = response.data['data']

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('daily_breakdown', data)
        self.assertGreaterEqual(len(data['weekly_breakdown']), 1)
        self.assertEqual(data['weekly_breakdown'][0]['week_start'], data['start_date'])
        self.assertEqual(data['weekly_breakdown'][-1]['week_end'], data['end_date'])
        self.assertEqual(data['end_date'], str(timezone.localdate()))
        self.assertEqual(data['top_products'][0]['product_name'], 'Milk')

    def test_staff_inventory_report_excludes_hidden_categories(self):
        hidden_category = Category.objects.create(
            name='Hidden Reports', is_visible_to_staff=False
        )
        Product.objects.create(
            category=hidden_category, name='Admin Product', unit='piece',
            unit_price=Decimal('5.00'), shelf_life=5,
        )
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/api/reports/preview/?type=inventory')
        names = [item['name'] for item in response.data['data']['items']]
        self.assertNotIn('Admin Product', names)
        self.assertEqual(response.data['data']['status_counts']['expiring_soon'], 1)

    def test_pdf_export_streams_a_pdf_attachment(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/api/reports/export-pdf/?type=inventory')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment;', response['Content-Disposition'])
        content = b''.join(response.streaming_content)
        self.assertTrue(content.startswith(b'%PDF'))
        self.assertGreater(len(content), 1000)

    def test_refresh_recalculates_all_report_types(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.post('/api/reports/refresh/', {}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['report_types']), 6)

    @patch('reporting.views.refresh_reports')
    def test_admin_refreshes_admin_and_staff_cache_scopes(self, refresh_mock):
        admin = User.objects.create_user(
            username='reportadmin', password='testpass123!',
            email='reportadmin@example.com', role='admin',
        )
        refreshed = {name: {} for name in (
            'daily_sales', 'weekly_sales', 'monthly_sales', 'inventory',
            'sarima_forecast', 'customer',
        )}
        refresh_mock.return_value = (timezone.now(), refreshed)
        self.client.force_authenticate(user=admin)

        response = self.client.post('/api/reports/refresh/', {}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            refresh_mock.call_args_list,
            [call(visible_to_staff=False), call(visible_to_staff=True)],
        )
