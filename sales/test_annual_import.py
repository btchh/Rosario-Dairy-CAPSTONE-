from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase
from django.utils import timezone
from openpyxl import Workbook
from rest_framework.test import APIClient

from accounts.models import Users
from inventory.models import ProductBatch
from sales.models import Customer, Order, Transaction, TransactionItem

from sales.importers.annual_sales import (
    SourceLine, SourceTicket, ParseResult, canonical_customer, parse_workbooks, source_date,
)
from sales.importers.load_annual_sales import load, merge_imported_customers


class AnnualSalesParserTests(TestCase):
    def test_written_2022_is_preserved_and_truncated_223_is_corrected(self):
        self.assertEqual(source_date('3/21/22', 3)[0], date(2022, 3, 21))
        self.assertEqual(source_date('2/16/223', 2), (date(2023, 2, 16), 'corrected_truncated_year'))
        self.assertEqual(source_date(datetime(2023, 2, 1), 1)[0], date(2023, 1, 2))

    def test_cancelled_name_keeps_customer_identity(self):
        self.assertEqual(canonical_customer('PCC-UPLB(CANCELLED)'), 'PCC-UPLB')
        self.assertIsNone(canonical_customer('CANCELLED'))

    def test_discounts_and_repeated_invoice_are_not_extra_products_or_tickets(self):
        with TemporaryDirectory() as directory:
            for year in (2023, 2024, 2025):
                workbook = Workbook()
                workbook.active.title = 'JANUARY'
                for month in range(2, 13):
                    workbook.create_sheet(f'MONTH-{month}')
                if year == 2023:
                    sheet = workbook.active
                    sheet.append(['Title'])
                    sheet.append([])
                    sheet.append(['Date', 'Customer', 'Sales Invoice #', 'Products', 'Quantity', 'U/P', 'Total Amount'])
                    sheet.append(['1/1/23', 'JCG', 'SI1', 'Raw Milk 1L', 10, 105, 1039.5, None])
                    sheet.append([None, None, 'SI1', 'CML', 1, 100, 100, None])
                    sheet.append([None, None, None, 'DISCOUNT', 1, -10, -10, 1129.5])
                    sheet.append(['TOTAL', None, None, None, None, None, 1129.5])
                workbook.save(Path(directory) / f'SALES REPORT JAN-DEC {year}.xlsx')
            result = parse_workbooks(directory)
        self.assertEqual(result.summary()['tickets'], 1)
        self.assertEqual(result.summary()['line_items'], 2)
        self.assertEqual(result.summary()['discount_rows'], 1)
        self.assertEqual(result.summary()['revenue'], '1129.50')


class AnnualSalesLoaderTests(TestCase):
    def setUp(self):
        self.admin = Users.objects.create_user(
            username='importadmin', email='importadmin@example.com',
            password='testpass123!', role='admin',
        )

    def test_load_preserves_links_dates_amounts_and_zero_stock(self):
        ticket = SourceTicket('2023', 'JANUARY', 4, date(2022, 12, 31), 'Skye', 'SI-12')
        ticket.lines.append(SourceLine(4, 'Raw Milk 1000ml', Decimal('10'),
                                       Decimal('105'), Decimal('1039.50')))
        result = ParseResult(tickets=[ticket], monthly_checks=[{'difference': '0.00'}] * 36)
        summary = load(result, self.admin.username)
        self.assertEqual(summary['transactions'], 1)
        self.assertEqual(summary['revenue'], '1039.50')
        self.assertEqual(Customer.objects.get().name, 'Skye Dairy')
        sale = Transaction.objects.get()
        self.assertEqual(timezone.localtime(sale.created_at).date(), date(2022, 12, 31))
        self.assertEqual(sale.source_invoice_number, 'SI-12')
        self.assertEqual(sale.payment_method, 'unknown')
        self.assertEqual(sale.discount_amount, Decimal('10.50'))
        self.assertEqual(Order.objects.get().transaction_id, sale.pk)
        item = TransactionItem.objects.get()
        self.assertEqual(item.source_product_label, 'Raw Milk 1000ml')
        self.assertEqual(item.source_line_total, Decimal('1039.50'))
        batch = ProductBatch.objects.get()
        self.assertTrue(batch.is_historical_reference)
        self.assertEqual(batch.remaining_quantity, Decimal('0.00'))
        client = APIClient()
        client.force_authenticate(user=self.admin)
        listed = client.get('/sales/orders/?page=1&page_size=5')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data['count'], 1)
        self.assertEqual(listed.data['results'][0]['transaction']['source_invoice_number'], 'SI-12')
        cancelled = client.post(f'/sales/orders/{Order.objects.get().pk}/cancel/')
        self.assertEqual(cancelled.status_code, 400)
        sale.refresh_from_db()
        self.assertFalse(sale.is_voided)

        duplicate = Customer.objects.create(name='SKYE DAIRY', created_by=self.admin)
        Transaction.objects.filter(pk=sale.pk).update(customer=duplicate)
        Order.objects.filter(transaction=sale).update(customer=duplicate)
        merged = merge_imported_customers(result)
        self.assertEqual(merged['merged'], 1)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Transaction.objects.get().customer.name, 'Skye Dairy')
        self.assertEqual(Order.objects.get().customer_id, Transaction.objects.get().customer_id)

    def test_refuses_to_mix_with_existing_business_data(self):
        Customer.objects.create(name='Existing', created_by=self.admin)
        with self.assertRaisesRegex(ValueError, 'not empty'):
            load(ParseResult(monthly_checks=[{'difference': '0.00'}] * 36), self.admin.username)
