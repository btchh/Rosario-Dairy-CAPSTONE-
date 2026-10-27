from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from reporting.pdf import Report
from reporting.serializers import CustomerReportSerializer
from reporting.services import customer_report, generate_pdf, get_report
from sales.models import Customer, Transaction


class ReportDateRangeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username='rangeadmin', email='ranges@example.test', role='admin',
        )
        self.customer = Customer.objects.create(name='School', created_by=self.user)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def sale(self, when, amount, customer=None, **kwargs):
        sale = Transaction.objects.create(
            handled_by=self.user, customer=customer, total_amount=Decimal(amount), **kwargs,
        )
        if isinstance(when, date) and not isinstance(when, datetime):
            when = datetime(when.year, when.month, when.day, 12, tzinfo=ZoneInfo('Asia/Manila'))
        Transaction.objects.filter(pk=sale.pk).update(created_at=when)
        return sale

    def preview(self, report_type='customer', **params):
        return self.client.get('/api/reports/preview/', {'type': report_type, **params})

    def test_default_is_current_month_and_every_purchasing_customer_is_present(self):
        today = timezone.localdate()
        self.sale(today.replace(day=1) - timedelta(days=1), '999', self.customer)
        for i in range(12):
            buyer = Customer.objects.create(name=f'Buyer {i}', created_by=self.user)
            self.sale(today, '10', buyer)
        response = self.preview()
        self.assertEqual(response.status_code, 200)
        data = response.data['data']
        self.assertEqual(data['start_date'], str(today.replace(day=1)))
        self.assertEqual(data['end_date'], str(today))
        self.assertEqual(len(data['customers']), 12)
        self.assertNotIn(self.customer.name, str(data['customers']))
        self.assertEqual(len(data['monthly_summary'][0]['customers']), 12)
        self.assertEqual(data['total_revenue'], '120.00')
        self.assertEqual(data['customers_with_purchases'], 12)

    def test_months_include_empty_buckets_and_clip_to_custom_dates(self):
        self.sale(date(2024, 1, 14), '999', self.customer)
        self.sale(date(2024, 1, 15), '100', self.customer, payment_status='paid')
        self.sale(date(2024, 3, 2), '30', self.customer, payment_status='pending')
        self.sale(date(2024, 3, 3), '999', self.customer)
        self.sale(date(2024, 2, 10), '999', self.customer, is_voided=True)
        self.sale(date(2024, 2, 11), '20')
        response = self.preview(start_date='2024-01-15', end_date='2024-03-02')
        self.assertEqual(response.status_code, 200)
        data = response.data['data']
        self.assertEqual(data['total_revenue'], '130.00')
        self.assertEqual(data['transaction_count'], 2)
        self.assertEqual(data['unassigned_revenue'], '20.00')
        months = data['monthly_summary']
        self.assertEqual([row['month'] for row in months], ['2024-01', '2024-02', '2024-03'])
        self.assertEqual([row['revenue'] for row in months], ['100.00', '0.00', '30.00'])
        self.assertEqual(months[0]['start_date'], '2024-01-15')
        self.assertEqual(months[-1]['end_date'], '2024-03-02')
        self.assertEqual(months[1]['customers'], [])
        self.assertEqual(sum(Decimal(row['revenue']) for row in months), Decimal(data['total_revenue']))

    def test_single_day_range_uses_manila_midnight_boundaries(self):
        manila = ZoneInfo('Asia/Manila')
        self.sale(datetime(2024, 1, 5, 23, 59, 59, tzinfo=manila), '999', self.customer)
        self.sale(datetime(2024, 1, 6, 0, 0, 0, tzinfo=manila), '10', self.customer)
        self.sale(datetime(2024, 1, 6, 23, 59, 59, tzinfo=manila), '20', self.customer)
        self.sale(datetime(2024, 1, 7, 0, 0, 0, tzinfo=manila), '999', self.customer)
        data = self.preview(start_date='2024-01-06', end_date='2024-01-06').data['data']
        self.assertEqual(data['total_revenue'], '30.00')
        self.assertEqual(data['transaction_count'], 2)

    def test_preview_and_pdf_use_same_dates_and_totals(self):
        self.sale(date(2024, 1, 31), '55', self.customer)
        params = {'type': 'customer', 'start_date': '2024-01-01', 'end_date': '2024-01-31'}
        preview = self.client.get('/api/reports/preview/', params).data['data']
        with patch('reporting.views.generate_pdf', wraps=generate_pdf) as render:
            response = self.client.get('/api/reports/export-pdf/', params)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(render.call_args.args[1], preview)
            self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))
            self.assertIn('2024-01-01-to-2024-01-31', response['Content-Disposition'])

    def test_range_is_part_of_report_cache_key(self):
        self.sale(date(2024, 1, 10), '10', self.customer)
        self.sale(date(2024, 2, 10), '20', self.customer)
        jan = get_report('customer', start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
        feb = get_report('customer', start_date=date(2024, 2, 1), end_date=date(2024, 2, 29))
        self.assertEqual(jan['total_revenue'], 10)
        self.assertEqual(feb['total_revenue'], 20)
        self.assertEqual(get_report('customer', start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))['total_revenue'], 10)

    def test_sales_report_types_support_custom_range(self):
        self.sale(date(2024, 1, 15), '50')
        self.sale(date(2024, 1, 16), '60')
        self.sale(date(2024, 1, 17), '999')
        for report_type in ('daily_sales', 'weekly_sales', 'monthly_sales'):
            with self.subTest(report_type=report_type):
                response = self.preview(report_type, start_date='2024-01-15', end_date='2024-01-16')
                self.assertEqual(response.status_code, 200)
                data = response.data['data']
                self.assertEqual(data.get('total_revenue', data.get('revenue')), '110.00')
                self.assertEqual(data['transaction_count'], 2)
                self.assertEqual(data['start_date'], '2024-01-15')
                self.assertEqual(data['end_date'], '2024-01-16')

    def test_invalid_ranges_are_rejected_by_both_endpoints(self):
        cases = [
            {'start_date': '2024-01-01'}, {'end_date': '2024-01-31'},
            {'start_date': 'wrong', 'end_date': '2024-01-31'},
            {'start_date': '2024-02-30', 'end_date': '2024-03-01'},
            {'start_date': '2024-02-01', 'end_date': '2024-01-01'},
            {'start_date': '2024-01-01', 'end_date': str(timezone.localdate() + timedelta(days=1))},
        ]
        for endpoint in ('preview', 'export-pdf'):
            for params in cases:
                with self.subTest(endpoint=endpoint, params=params):
                    response = self.client.get(f'/api/reports/{endpoint}/', {'type': 'customer', **params})
                    self.assertEqual(response.status_code, 400, response.data)

    def test_historical_payment_status_is_current_status(self):
        sale = self.sale(date(2024, 1, 10), '25', self.customer, payment_status='pending')
        Transaction.objects.filter(pk=sale.pk).update(payment_status='paid')
        report = customer_report(start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
        self.assertEqual(report['paid_amount'], 25)
        self.assertEqual(report['pending_amount'], 0)

    def test_single_month_pdf_has_no_month_overview_or_duplicate_month_table(self):
        self.sale(date(2024, 1, 15), '10', self.customer)
        data = CustomerReportSerializer(customer_report(start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))).data
        report = Report('customer', data)
        report.customer()
        headings = [part.text for part in report.story if hasattr(part, 'text')]
        self.assertIn('Customer purchase and payment summary', headings)
        self.assertNotIn('Month-by-month overview', headings)
        self.assertFalse(any('Customer summary —' in text for text in headings))
        self.assertNotIn('Payment follow-up for selected period', headings)
        self.assertNotIn('Sales without a customer account', headings)

    def test_multiple_month_pdf_uses_clear_month_names_and_skips_empty_months(self):
        self.sale(date(2024, 1, 15), '10', self.customer)
        self.sale(date(2024, 3, 2), '20', self.customer)
        data = CustomerReportSerializer(customer_report(start_date=date(2024, 1, 1), end_date=date(2024, 3, 31))).data
        report = Report('customer', data)
        report.customer()
        headings = [part.text for part in report.story if hasattr(part, 'text')]
        self.assertIn('Month-by-month overview', headings)
        self.assertIn('Customer summary — January 2024', headings)
        self.assertIn('Customer summary — March 2024', headings)
        self.assertNotIn('Customer summary — February 2024', headings)
        self.assertTrue(Report('customer', data).render().read().startswith(b'%PDF'))

    def test_zero_value_invoice_is_included_and_no_transaction_accounts_are_omitted(self):
        self.sale(date(2024, 1, 15), '0', self.customer)
        Customer.objects.create(name='Never purchased', created_by=self.user)
        data = self.preview(start_date='2024-01-01', end_date='2024-01-31').data['data']
        self.assertEqual(len(data['customers']), 1)
        self.assertEqual(data['customers'][0]['customer_id'], self.customer.pk)
        self.assertEqual(data['customers'][0]['transaction_count'], 1)
        self.assertEqual(data['total_revenue'], '0.00')
