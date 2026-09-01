from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from inventory.models import Category, Ingredient, IngredientBatch, Product, ProductBatch
from sales.models import Customer, Transaction, TransactionItem


User = get_user_model()


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
        self.assertEqual(data['items'][0]['date'], str(timezone.localdate()))

    def test_daily_sales_pdf_contains_product_breakdown(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get('/api/reports/export-pdf/?type=daily_sales')
        content = b''.join(response.streaming_content)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(content.startswith(b'%PDF'))
        self.assertGreater(len(content), 2000)

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
