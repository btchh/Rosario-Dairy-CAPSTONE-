from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from openpyxl import Workbook
from rest_framework.test import APIClient

from accounts.models import Users
from inventory.models import Category, Product, ProductBatch
from sales.models import Customer, Order, Transaction, TransactionItem
from sales.importers.annual_sales import SourceTicket, SourceLine, ParseResult, parse_workbooks
from sales.importers.refresh_annual_sales import build_plan, apply_plan


def line(row, name, amount='100', price='100'):
    return SourceLine(row, name, Decimal('1'), Decimal(price) if price is not None else None,
                      Decimal(amount) if amount is not None else None)


def ticket(row=4, customer='Shop', invoice='SI1', lines=None, flags=None):
    return SourceTicket('2025', 'JANUARY', row, date(2025, 1, 26), customer, invoice,
                        lines=lines if lines is not None else [line(row, 'FML')],
                        quality_flags=flags or [])


def parsed(*tickets):
    return ParseResult(tickets=list(tickets), monthly_checks=[{'difference': '0.00'}] * 36)


class DairyPlanTests(SimpleTestCase):
    def test_user_excluded_sweets_and_non_dairy_labels(self):
        names = ['PDLX20', 'PDL CHZ', 'Pastillas 50g', 'Pastiroll', 'Polvoron',
                 'DC', 'ICE', '12 in 1', 'Chocovron', 'Banana Chips']
        source = parsed(ticket(lines=[line(i + 4, name) for i, name in enumerate(names)]))
        plan, audit = build_plan(source)
        self.assertEqual(plan, [])
        self.assertEqual(audit['summary']['excluded_lines'], len(names))
        self.assertEqual(audit['summary']['excluded_revenue'], '1000.00')

    def test_mixed_invoice_allocates_discount_and_reconciles_every_cent(self):
        source_ticket = ticket(lines=[line(4, 'FML', '200', '200'), line(5, 'PDLX20', '50', '50')])
        source_ticket.adjustments = [Decimal('-25')]
        plan, audit = build_plan(parsed(source_ticket))
        self.assertEqual(plan[0].amount, Decimal('180'))
        self.assertEqual(audit['monthly'][0]['excluded_revenue'], '45.00')
        self.assertEqual(Decimal(audit['summary']['source_revenue']),
                         Decimal(audit['summary']['dairy_revenue']) + Decimal(audit['summary']['excluded_revenue']))

    def test_unknown_product_is_excluded_with_review_record(self):
        plan, audit = build_plan(parsed(ticket(lines=[line(4, 'Never reviewed')])) )
        self.assertEqual(plan, [])
        self.assertEqual(audit['review'][0]['decision'], 'review')

    def test_missing_prices_on_mixed_invoice_hold_total_without_guessing(self):
        entry = ticket(lines=[line(4, 'FML', None, None), line(5, 'PDLX20', None, None)])
        entry.reported_total = Decimal('69475')
        plan, audit = build_plan(parsed(entry))
        self.assertEqual(plan, [])
        self.assertEqual(audit['summary']['excluded_revenue'], '69475.00')
        self.assertEqual(audit['review'][0]['decision'], 'invoice_hold')
        self.assertEqual(len(audit['held_dairy_lines']), 1)

    def test_unlabelled_and_mismatched_date_entries_are_unclassified(self):
        source = parsed(ticket(customer='', invoice='', flags=['unlabelled_separate_total']),
                        ticket(5, flags=['date_sheet_mismatch']))
        plan, _ = build_plan(source)
        self.assertEqual([t.segment for t in plan], ['unclassified', 'unclassified'])

    def test_refuses_incomplete_or_unreconciled_sources(self):
        source = parsed(ticket()); source.monthly_checks[0] = {'difference': '1.00'}
        with self.assertRaisesRegex(ValueError, '36 source sheet'):
            build_plan(source)

    def test_parser_separates_explicit_invoice_sum_from_unlabelled_self_total(self):
        with TemporaryDirectory() as directory:
            for year in (2023, 2024, 2025):
                book = Workbook(); book.active.title = 'JANUARY'
                for month in range(2, 13): book.create_sheet(f'MONTH-{month}')
                if year == 2025:
                    sheet = book.active
                    sheet.append(['Title']); sheet.append([]); sheet.append(['Date'])
                    sheet.append(['1/26/25', 'PCC-UPLB', 'SI03744', 'FML', 2, 100, 200])
                    sheet.append([None, None, None, 'PDLX20', 1, 50, 50, '=SUM(G4:G5)'])
                    sheet.append([None, None, None, 'Toned Milk', 3, 100, 300, '=G6'])
                    sheet.append(['1/27/25', 'Milk Feeding-DEPED', '', 'TM200', 10, 20, 200, '=G7'])
                    sheet.append([None, None, None, 'CMTM200ML', 10, 18, 180, '=G8'])
                book.save(Path(directory) / f'SALES REPORT JAN-DEC {year}.xlsx')
            result = parse_workbooks(directory, separate_unlabelled_totals=True)
        self.assertEqual([t.reference for t in result.tickets], ['2025:JANUARY:4', '2025:JANUARY:6', '2025:JANUARY:7'])
        self.assertEqual(result.tickets[0].amount, Decimal('250'))
        self.assertEqual(result.tickets[1].customer_label, '')
        self.assertEqual(result.tickets[1].invoice_number, '')
        self.assertEqual(result.tickets[1].amount, Decimal('300'))
        self.assertEqual(len(result.tickets[2].lines), 2)


class DairyRefreshTests(TestCase):
    def setUp(self):
        self.admin = Users.objects.create_user(username='refresh-admin', email='refresh@example.test', role='admin')
        self.customer = Customer.objects.create(name='Shop', created_by=self.admin)
        self.category = Category.objects.create(name='Dairy')
        self.milk = Product.objects.create(name='FML', category=self.category, unit='unit', shelf_life=7)
        self.sweets = Product.objects.create(name='PDLX20', category=self.category, unit='unit', shelf_life=7)
        self.batch = ProductBatch.objects.create(product=self.milk, batch_number='HIST', initial_quantity=0,
                      remaining_quantity=0, expiration_date=date(2025, 1, 1), is_historical_reference=True, status='depleted')
        self.live_batch = ProductBatch.objects.create(product=self.milk, batch_number='LIVE', initial_quantity=100,
                      remaining_quantity=90, expiration_date=date(2026, 12, 1))

    def sale(self, ref, amount='100', batch=None, **kwargs):
        sale = Transaction.objects.create(handled_by=self.admin, customer=self.customer,
                 source_reference=ref, total_amount=Decimal(amount), **kwargs)
        TransactionItem.objects.create(transaction=sale, product_batch=batch or self.batch,
                                       quantity=1, unit_price=100)
        return sale

    def apply(self, source):
        plan, _ = build_plan(source)
        return apply_plan(source, plan, self.admin.username)

    def test_refresh_preserves_receipt_identity_payments_and_live_data_and_is_idempotent(self):
        historical = self.sale('2025:JANUARY:4', '250', payment_status='paid', amount_tendered=250,
                               source_invoice_number='SI1')
        manual = self.sale(None, batch=self.live_batch, encoded_at=timezone.now())
        demo = self.sale('2026:000001')
        source = parsed(ticket(lines=[line(4, 'FML', '200', '200'), line(5, 'PDLX20', '50', '50')]))
        source.tickets[0].adjustments = [Decimal('-25')]
        self.apply(source)
        historical.refresh_from_db()
        self.assertEqual(historical.total_amount, Decimal('180'))
        self.assertEqual(historical.payment_status, 'paid')
        self.assertEqual(historical.amount_tendered, Decimal('250'))
        self.assertEqual(historical.source_invoice_number, 'SI1')
        self.assertEqual(historical.source_sales_segment, 'regular')
        self.assertEqual(historical.items.count(), 1)
        order = Order.objects.get(transaction=historical)
        self.assertEqual(order.items.count(), 1)
        item_ids = list(historical.items.values_list('pk', flat=True))
        order_ids = list(order.items.values_list('pk', flat=True))
        repeated = self.apply(source)
        self.assertEqual(repeated.get('unchanged_transactions'), 1)
        self.assertEqual(repeated.get('replaced_line_sets', 0), 0)
        self.assertEqual(list(historical.items.values_list('pk', flat=True)), item_ids)
        self.assertEqual(list(order.items.values_list('pk', flat=True)), order_ids)
        manual.refresh_from_db(); demo.refresh_from_db(); self.live_batch.refresh_from_db()
        self.assertEqual(manual.total_amount, 100)
        self.assertEqual(demo.total_amount, 100)
        self.assertEqual(self.live_batch.remaining_quantity, 90)
        self.assertEqual(Product.objects.count(), 2)

    def test_removes_only_source_non_dairy_only_receipt(self):
        old = self.sale('2025:JANUARY:4')
        manual = self.sale(None, batch=self.live_batch)
        self.apply(parsed(ticket(lines=[line(4, 'PDLX20')])))
        self.assertFalse(Transaction.objects.filter(pk=old.pk).exists())
        self.assertTrue(Transaction.objects.filter(pk=manual.pk).exists())

    def test_unlabelled_dairy_is_retained_without_fabricated_customer_or_si(self):
        self.apply(parsed(ticket(customer='', invoice='', flags=['unlabelled_separate_total'])))
        sale = Transaction.objects.get()
        self.assertIsNone(sale.customer_id)
        self.assertIsNone(sale.source_invoice_number)
        client = APIClient(); client.force_authenticate(self.admin)
        response = client.get(f'/sales/transactions/{sale.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['customer'])
        self.assertIsNone(response.data['source_invoice_number'])
        self.assertIsNone(response.data['transaction_number'])
        response = client.get('/api/reports/preview/', {
            'type': 'customer', 'start_date': '2025-01-01', 'end_date': '2025-01-31'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['unassigned_transaction_count'], 1)
        self.assertEqual(Decimal(response.data['data']['unassigned_revenue']), Decimal('100'))
        self.assertEqual(response.data['data']['customers'], [])
        self.assertEqual(sale.source_sales_segment, 'unclassified')
        self.assertEqual(Order.objects.count(), 0)
        self.apply(parsed(ticket(customer='', invoice='', flags=['unlabelled_separate_total'])))
        sale.refresh_from_db()
        self.assertIsNone(sale.customer_id)
        self.assertIsNone(sale.source_invoice_number)

    def test_refuses_to_overwrite_manually_encoded_or_stock_affecting_receipt(self):
        sale = self.sale('2025:JANUARY:4', encoded_at=timezone.now())
        with self.assertRaisesRegex(ValueError, 'Manually encoded'):
            self.apply(parsed(ticket()))
        Transaction.objects.filter(pk=sale.pk).update(encoded_at=None)
        sale.items.update(product_batch=self.live_batch)
        with self.assertRaisesRegex(ValueError, 'live stock'):
            self.apply(parsed(ticket()))

    def test_failure_rolls_back_transactions_orders_and_lines(self):
        sale = self.sale('2025:JANUARY:4', '250')
        before_ids = list(sale.items.values_list('pk', flat=True))
        with patch.object(TransactionItem.objects, 'bulk_create', side_effect=IntegrityError('simulated')):
            with self.assertRaises(IntegrityError):
                self.apply(parsed(ticket()))
        sale.refresh_from_db()
        self.assertEqual(sale.total_amount, 250)
        self.assertEqual(list(sale.items.values_list('pk', flat=True)), before_ids)
        self.assertEqual(Order.objects.count(), 0)
