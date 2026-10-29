from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from inventory.models import Category, FEFOConf, Product, ProductBatch, StockAdjustment
from reporting.pdf import Report
from reporting.services import generate_pdf, get_report, inventory_ledger, sarima_forecast
from sales.models import Customer, StockReturn, Transaction, TransactionItem


def stamp(day, hour=12, minute=0, second=0):
    return datetime(2024, 1, day, hour, minute, second, tzinfo=ZoneInfo('Asia/Manila'))


class InventoryRangeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(username='inventory-ranges', role='admin')
        self.customer = Customer.objects.create(name='School', created_by=self.admin)
        self.product = Product.objects.create(name='Milk', unit='piece', shelf_life=30,
            category=Category.objects.create(name='Dairy'))
        self.batch = self.batch_on(1, 100, 80)
        self.sale(5, 5)
        cancelled = self.sale(10, 10, is_voided=True)
        returned = StockReturn.objects.create(sale_item=cancelled, quantity=3)
        StockReturn.objects.filter(pk=returned.pk).update(created_at=stamp(25))
        self.sale(20, 7)
        self.adjustment(12, 2)
        self.adjustment(22, -1)
        second = self.batch_on(11, 20, 18)
        # A backdated invoice physically deducted inventory on the entry date.
        self.sale(2, 2, batch=second, customer=self.customer, encoded_at=stamp(13))
        self.batch_on(21, 50, 50)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def batch_on(self, day, initial, remaining):
        batch = ProductBatch.objects.create(product=self.product, batch_number=f'RANGE-{day}',
            date_received=date(2024, 1, day), initial_quantity=initial, remaining_quantity=remaining,
            expiration_date=date(2024, 2, 1))
        ProductBatch.objects.filter(pk=batch.pk).update(created_at=stamp(day))
        return batch

    def sale(self, day, quantity, batch=None, **kwargs):
        sale = Transaction.objects.create(handled_by=self.admin, **kwargs)
        Transaction.objects.filter(pk=sale.pk).update(created_at=stamp(day))
        return TransactionItem.objects.create(transaction=sale, product_batch=batch or self.batch,
            quantity=quantity, unit_price=10)

    def adjustment(self, day, quantity):
        adjustment = StockAdjustment.objects.create(product_batch=self.batch,
            adjustment_type='correction', quantity=quantity, unit_cost=10, adjusted_by=self.admin)
        StockAdjustment.objects.filter(pk=adjustment.pk).update(created_at=stamp(day))

    def test_period_balances_reverse_later_sales_adjustments_and_partial_returns(self):
        ledger = inventory_ledger(start_date=date(2024, 1, 10), end_date=date(2024, 1, 15))
        row = ledger['rows'][0]
        self.assertEqual((row['beginning'], row['received'], row['total'], row['ending']), (95, 20, 115, 101))
        self.assertEqual(row['outlet'], 10)
        self.assertEqual(row['allocations'], {f'customer-{self.customer.pk}': 2})
        self.assertEqual(row['adjustments'], 2)
        self.assertEqual(row['total'] - row['outlet'] - sum(row['allocations'].values()) - row['adjustments'], row['ending'])
        self.assertEqual(ledger['start_date'], date(2024, 1, 10))
        self.assertEqual(ledger['end_date'], date(2024, 1, 15))

    def test_cancellation_credit_uses_its_own_selected_date(self):
        row = inventory_ledger(start_date=date(2024, 1, 25), end_date=date(2024, 1, 25))['rows'][0]
        self.assertEqual(row['adjustments'], -3)
        self.assertEqual(row['outlet'], 0)
        self.assertEqual(row['ending'] - row['beginning'], 3)

    def test_range_cache_and_same_day_manila_boundaries(self):
        for when, quantity in [(stamp(9, 23, 59, 59), 100), (stamp(10, 0), 1),
                               (stamp(10, 23, 59, 59), 2), (stamp(11, 0), 200)]:
            item = self.sale(10, quantity)
            Transaction.objects.filter(pk=item.transaction_id).update(created_at=when)
        params = dict(start_date=date(2024, 1, 10), end_date=date(2024, 1, 10))
        first = get_report('inventory', **params)
        self.assertEqual(first['ledger']['rows'][0]['outlet'], 13)
        second = get_report('inventory', start_date=date(2024, 1, 11), end_date=date(2024, 1, 11))
        self.assertEqual(second['ledger']['rows'][0]['outlet'], 200)
        self.assertEqual(get_report('inventory', **params)['ledger'], first['ledger'])

    def test_preview_pdf_and_filename_use_the_same_range_and_separate_current_health(self):
        # Prepare health settings before taking the preview snapshot.
        FEFOConf.get_config()
        params = {'type': 'inventory', 'start_date': '2024-01-10', 'end_date': '2024-01-15'}
        preview = self.client.get('/api/reports/preview/', params)
        self.assertEqual(preview.status_code, 200)
        data = preview.data['data']
        self.assertEqual((data['start_date'], data['end_date']), ('2024-01-10', '2024-01-15'))
        self.assertEqual(data['ledger']['rows'][0]['ending'], '101.00')
        self.assertTrue(data['as_of'].startswith(str(timezone.localdate())))
        with patch('reporting.views.generate_pdf', wraps=generate_pdf) as render:
            response = self.client.get('/api/reports/export-pdf/', params)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(render.call_args.args[1], data)
            self.assertIn('2024-01-10-to-2024-01-15', response['Content-Disposition'])
            self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))
        report = Report('inventory', data)
        report.header()
        report.inventory()
        text = ' '.join(part.text for part in report.story if hasattr(part, 'text'))
        self.assertIn('2024-01-10 to 2024-01-15', text)
        self.assertIn('Inventory movement report', text)
        self.assertIn('Current inventory health', text)

    def test_custom_staff_ledger_keeps_mixed_invoice_buyers_private(self):
        hidden = Product.objects.create(name='Hidden', unit='piece', shelf_life=30,
            category=Category.objects.create(name='Hidden', is_visible_to_staff=False))
        hidden_batch = ProductBatch.objects.create(product=hidden, batch_number='RANGE-HIDDEN',
            date_received=date(2024, 1, 1), initial_quantity=10, remaining_quantity=9,
            expiration_date=date(2024, 2, 1))
        ProductBatch.objects.filter(pk=hidden_batch.pk).update(created_at=stamp(1))
        item = self.sale(14, 1, customer=self.customer)
        TransactionItem.objects.create(transaction=item.transaction, product_batch=hidden_batch, quantity=1, unit_price=10)
        ledger = inventory_ledger(True, date(2024, 1, 14), date(2024, 1, 14))
        self.assertEqual(len(ledger['rows']), 1)
        self.assertEqual(ledger['columns'], [{'key': 'restricted', 'name': 'Other stock deductions'}])
        self.assertNotIn(self.customer.name, str(ledger))


class ForecastRangeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(username='forecast-ranges', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.payload = {
            'generated_at': timezone.now(), 'horizon_days': 30, 'method': 'sarima',
            'is_placeholder': False, 'status': 'ready', 'period': 'monthly',
            'regular': {'status': 'ready', 'predicted_revenue': Decimal('120')},
            'forecast': [{'date': date(2024, 1, 1), 'end_date': date(2024, 1, 31),
                          'predicted_revenue': Decimal('120'), 'lower_bound': Decimal('100'),
                          'upper_bound': Decimal('140')}],
        }

    def test_overlapping_month_preserves_full_value_and_actual_forecast_dates(self):
        with patch('forecasting.serving.report', return_value=self.payload):
            result = sarima_forecast(start_date=date(2024, 1, 15), end_date=date(2024, 1, 20))
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['forecast'][0]['date'], date(2024, 1, 1))
        self.assertEqual(result['forecast'][0]['predicted_revenue'], 120)
        self.assertIn('not prorated', result['range_note'])
        self.assertNotIn('start_date', self.payload)

    def test_outside_range_removes_prediction_and_summary_amount(self):
        with patch('forecasting.serving.report', return_value=self.payload):
            result = sarima_forecast(start_date=date(2024, 2, 1), end_date=date(2024, 2, 29))
        self.assertEqual(result['forecast'], [])
        self.assertEqual(result['status'], 'no_forecasts_in_range')
        self.assertNotIn('predicted_revenue', result['regular'])
        self.assertIn('No approved forecast', result['range_note'])
        self.assertEqual(self.payload['regular']['predicted_revenue'], 120)

    def test_unavailable_model_keeps_quality_status_and_selected_dates(self):
        payload = {**self.payload, 'status': 'stale_data', 'forecast': [], 'regular': {'status': 'stale_data'}}
        with patch('forecasting.serving.report', return_value=payload):
            result = sarima_forecast(start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))
        self.assertEqual(result['status'], 'stale_data')
        self.assertEqual(result['start_date'], date(2024, 1, 1))
        self.assertEqual(result['forecast'], [])
        self.assertIn('No approved forecast', result['range_note'])

    def test_preview_pdf_and_cache_honor_dates_without_training(self):
        params = {'type': 'sarima_forecast', 'start_date': '2024-01-15', 'end_date': '2024-01-20'}
        with patch('forecasting.serving.report', return_value=self.payload) as saved_report, \
                patch('reporting.views.generate_pdf', wraps=generate_pdf) as render:
            response = self.client.get('/api/reports/preview/', params)
            self.assertEqual(response.status_code, 200)
            preview = response.data['data']
            response = self.client.get('/api/reports/export-pdf/', params)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(render.call_args.args[1], preview)
            self.assertIn('2024-01-15-to-2024-01-20', response['Content-Disposition'])
            self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))
            self.assertEqual(saved_report.call_count, 1)
            response = self.client.get('/api/reports/preview/', {**params, 'start_date': '2024-02-01', 'end_date': '2024-02-29'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['data']['forecast'], [])
            self.assertEqual(saved_report.call_count, 2)

    def test_staff_cannot_request_custom_global_forecasts(self):
        self.admin.role = 'staff'
        self.admin.save(update_fields=['role'])
        for endpoint in ('preview', 'export-pdf'):
            response = self.client.get(f'/api/reports/{endpoint}/', {
                'type': 'sarima_forecast', 'start_date': '2024-01-01', 'end_date': '2024-01-31'})
            self.assertEqual(response.status_code, 403)
