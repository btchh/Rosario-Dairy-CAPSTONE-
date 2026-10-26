from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from inventory.models import Category, Product, ProductBatch
from reporting.pdf import Report
from reporting.services import customer_report, daily_sales, inventory_status, monthly_sales, weekly_sales
from sales.models import Customer, Transaction, TransactionItem


class CompleteReportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username='complete-reports', email='complete@example.test', role='admin',
        )
        for index in range(23):
            category = Category.objects.create(name=f'Category {index:02}')
            product = Product.objects.create(category=category, name=f'Product {index:02}', unit='piece', shelf_life=7)
            batch = ProductBatch.objects.create(
                product=product, batch_number=f'COMPLETE-{index}', initial_quantity=2,
                remaining_quantity=1, expiration_date=timezone.localdate() + timedelta(days=3),
            )
            customer = Customer.objects.create(name=f'Customer {index:02}', created_by=self.user)
            sale = Transaction.objects.create(handled_by=self.user, customer=customer, subtotal=index + 1, total_amount=index + 1)
            TransactionItem.objects.create(transaction=sale, product_batch=batch, quantity=1, unit_price=index + 1)

    def test_sales_reports_include_all_products_families_and_categories(self):
        for builder in (daily_sales, weekly_sales, monthly_sales):
            with self.subTest(report=builder.__name__):
                data = builder()
                products = data.get('items', data.get('top_products'))
                self.assertEqual(len(products), 23)
                self.assertEqual(len(data['family_mix']), 23)
                self.assertEqual(len(data['category_mix']), 23)
                self.assertEqual({p['product_name'] for p in products}, {f'Product {i:02}' for i in range(23)})
                self.assertEqual(sum(p['gross_sales'] for p in data['category_mix']), data['gross_sales'])

    def test_compatibility_customer_list_has_no_top_ten_limit(self):
        data = customer_report()
        self.assertEqual(len(data['customers']), 23)
        self.assertEqual(len(data['top_customers']), 23)
        self.assertEqual(len(data['monthly_summary'][0]['customers']), 23)

    def test_priority_stock_review_includes_every_exception_and_preserves_ledger(self):
        data = inventory_status()
        report = Report('inventory', data)
        with patch.object(report, 'table', wraps=report.table) as table, patch.object(report, 'inventory_ledger') as ledger:
            report.inventory()
        ledger.assert_called_once()
        priority = next(call for call in table.call_args_list if call.args[0][-1] == 'Reason')
        self.assertEqual(len(priority.args[1]), 23)
        self.assertTrue(Report('inventory', data).render().read().startswith(b'%PDF'))

    def test_forecast_pdf_shows_useful_availability_without_internal_diagnostics(self):
        report = Report('sarima_forecast', {
            'status': 'stale_data', 'period': 'monthly', 'data_end': '2025-12-31',
            'status_message': 'Internal model publication gate details',
            'accuracy_target_percent': 15, 'limitations': 'Internal calibration details',
            'warnings': ['stale_training_data', 'unverified_ledger_coverage',
                         'needs_12_prospective_reviewed_months'],
        })
        report.forecast()
        content = ' '.join(part.text for part in report.story if hasattr(part, 'text'))
        self.assertIn('Recent sales are needed', content)
        self.assertIn('2025-12-31', content)
        for text in ('Forecasting status', 'Internal', 'Research target', *report.data['warnings']):
            self.assertNotIn(text, content)
        self.assertTrue(Report('sarima_forecast', report.data).render().read().startswith(b'%PDF'))

    def test_available_forecast_keeps_amount_dates_and_regular_sales_scope(self):
        report = Report('sarima_forecast', {
            'status': 'ready', 'period': 'monthly', 'regular': {'predicted_revenue': 0},
            'forecast': [{'date': '2026-11-01', 'end_date': '2026-11-30'}],
        })
        with patch.object(report, 'cards', wraps=report.cards) as cards:
            report.forecast()
        values = dict(cards.call_args.args[0])
        self.assertEqual(values['Forecast status'], 'Available')
        self.assertIn('0.00', values['Regular dairy forecast'])
        content = ' '.join(part.text for part in report.story if hasattr(part, 'text'))
        self.assertIn('2026-11-01 to 2026-11-30', content)
        self.assertIn('milk feeding is not included', content)
