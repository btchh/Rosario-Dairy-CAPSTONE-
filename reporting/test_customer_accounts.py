from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from reportlab.lib.pagesizes import A4, landscape
from rest_framework.test import APIClient

from inventory.models import Category, Product, ProductBatch
from reporting.pdf import Report
from reporting.serializers import CustomerReportSerializer
from reporting.services import customer_report
from sales.models import Customer, Transaction, TransactionItem


class CustomerAccountReportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username='accountadmin', email='accounts@example.test', role='admin',
        )
        self.customer = Customer.objects.create(name='School', created_by=self.user)

    def sale(self, customer, total, status='paid', **kwargs):
        return Transaction.objects.create(
            handled_by=self.user, customer=customer, total_amount=Decimal(total),
            payment_status=status, **kwargs,
        )

    def test_payment_status_totals_reconcile_and_account_activity_is_reported(self):
        self.sale(self.customer, '100', 'paid')
        self.sale(self.customer, '50', 'pending')
        self.sale(self.customer, '25', 'unknown')
        report = customer_report()
        row = report['customers'][0]
        self.assertEqual(row['transaction_count'], 3)
        self.assertEqual(row['total_spent'], 175)
        self.assertEqual(row['paid_amount'] + row['pending_amount'] + row['unknown_amount'], row['total_spent'])
        self.assertEqual((report['paid_amount'], report['pending_amount'], report['unknown_amount']), (100, 50, 25))
        self.assertEqual(report['pending_customer_count'], 1)
        self.assertEqual(row['pending_count'], 1)
        self.assertEqual(row['last_purchase'], timezone.localdate())
        self.assertEqual(report['repeat_customer_count'], 1)

    def test_duplicate_names_remain_separate_and_only_purchasers_are_listed(self):
        other = Customer.objects.create(name='School', created_by=self.user)
        inactive = Customer.objects.create(name='Closed', is_active=False, created_by=self.user)
        self.sale(self.customer, '100')
        self.sale(other, '20')
        self.sale(inactive, '10', 'pending')
        Customer.objects.create(name='New buyer', created_by=self.user)
        report = customer_report()
        self.assertEqual(report['customer_record_count'], 3)
        self.assertEqual(report['total_customers'], 3)
        schools = [r for r in report['customers'] if r['customer_name'] == 'School']
        self.assertEqual({r['customer_id'] for r in schools}, {self.customer.pk, other.pk})
        self.assertEqual(len(report['top_customers']), 3)
        self.assertEqual(report['total_lifetime_value'], 130)
        self.assertNotIn('New buyer', str(report['customers']))

    def test_voids_and_future_sales_are_excluded_and_unassigned_sales_are_separate(self):
        self.sale(self.customer, '200', is_voided=True)
        future = self.sale(self.customer, '300', 'pending')
        Transaction.objects.filter(pk=future.pk).update(created_at=timezone.now() + timedelta(days=1))
        self.sale(None, '50', source_customer_label='Imported outlet')
        report = customer_report()
        self.assertEqual(report['total_lifetime_value'], 0)
        self.assertEqual(report['customers_with_purchases'], 0)
        self.assertEqual(report['active_customer_count'], 0)
        self.assertEqual(report['pending_amount'], 0)
        self.assertEqual(report['unassigned_revenue'], 50)
        self.assertEqual(report['unassigned_transaction_count'], 1)

    def test_old_sales_are_not_counted_as_recent(self):
        sale = self.sale(self.customer, '10')
        Transaction.objects.filter(pk=sale.pk).update(created_at=timezone.now() - timedelta(days=90))
        report = customer_report()
        self.assertEqual(report['customers_with_purchases'], 0)
        self.assertEqual(report['total_revenue'], 0)
        self.assertEqual(report['total_lifetime_value'], 10)
        self.assertEqual(report['active_customer_count'], 0)

    def test_staff_payment_totals_exclude_hidden_sales(self):
        sale = self.sale(self.customer, '100', 'pending')
        category = Category.objects.create(name='Private', is_visible_to_staff=False)
        product = Product.objects.create(category=category, name='Private milk', unit='piece', shelf_life=7)
        batch = ProductBatch.objects.create(
            product=product, batch_number='CUSTOMER-PRIVATE', initial_quantity=10,
            remaining_quantity=9, expiration_date=timezone.localdate() + timedelta(days=7),
        )
        TransactionItem.objects.create(transaction=sale, product_batch=batch, quantity=1, unit_price=100)
        self.assertEqual(customer_report()['pending_amount'], 100)
        staff_report = customer_report(True)
        self.assertEqual(staff_report['pending_amount'], 0)
        self.assertNotIn(self.customer.name, str(staff_report))
        self.assertEqual(staff_report['customers'], [])

    def test_preview_pdf_and_empty_report(self):
        self.sale(self.customer, '100', 'pending')
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.get('/api/reports/preview/?type=customer')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['customers'][0]['pending_amount'], '100.00')
        response = client.get('/api/reports/export-pdf/?type=customer')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))
        report = Report('customer', CustomerReportSerializer(customer_report()).data)
        self.assertEqual(report.page_size, landscape(A4))
        Transaction.objects.all().delete()
        Customer.objects.all().delete()
        cache.clear()
        data = CustomerReportSerializer(customer_report()).data
        self.assertEqual(data['customers'], [])
        self.assertTrue(Report('customer', data).render().read().startswith(b'%PDF'))
