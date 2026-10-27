from datetime import timedelta
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from inventory.models import Category, Product, ProductBatch, StockAdjustment
from reporting.pdf import FooterCanvas
from reporting.serializers import ReportTypeQuerySerializer


class ReportComparisonCalendarTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(
            username='report-calendar-admin', role='admin',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_sales_preview_and_pdf_reject_unrepresentable_comparison_before_building(self):
        for endpoint in ('preview', 'export-pdf'):
            for report_type in ('daily_sales', 'weekly_sales', 'monthly_sales'):
                with self.subTest(endpoint=endpoint, report_type=report_type):
                    with patch('reporting.views.get_report') as build:
                        response = self.client.get(f'/api/reports/{endpoint}/', {
                            'type': report_type,
                            'start_date': '0002-01-01', 'end_date': '2024-01-01',
                        })
                    self.assertEqual(response.status_code, 400, response.data)
                    self.assertIn('start_date', response.data)
                    build.assert_not_called()

    def test_sales_range_accepts_earliest_representable_comparison(self):
        for report_type in ('daily_sales', 'weekly_sales', 'monthly_sales'):
            with self.subTest(report_type=report_type):
                query = ReportTypeQuerySerializer(data={
                    'type': report_type,
                    # Its preceding comparison begins on 0001-01-01.
                    'start_date': '0002-01-01', 'end_date': '0002-12-31',
                })
                self.assertTrue(query.is_valid(), query.errors)

    def test_noncomparison_reports_preserve_their_historical_range(self):
        for report_type in ('customer', 'inventory', 'sarima_forecast'):
            with self.subTest(report_type=report_type):
                query = ReportTypeQuerySerializer(data={
                    'type': report_type,
                    'start_date': '0002-01-01', 'end_date': '2024-01-01',
                })
                self.assertTrue(query.is_valid(), query.errors)


class InventoryPDFLongRemarksTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_user(
            username='inventory-long-remarks-admin', role='admin',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_adjustment_remarks_continue_across_pdf_pages_without_losing_text(self):
        today = timezone.localdate()
        product = Product.objects.create(
            name='Milk', unit='piece', shelf_life=30,
            category=Category.objects.create(name='Dairy'),
        )
        batch = ProductBatch.objects.create(
            product=product, batch_number='AUDIT-LONG-REMARKS',
            date_received=today, expiration_date=today + timedelta(days=30),
            initial_quantity=100, remaining_quantity=50,
        )
        markers = [f'RECONCILE{index:03}' for index in range(50)]
        for marker in markers:
            StockAdjustment.objects.create(
                product_batch=batch, adjustment_type='correction', quantity=1,
                unit_cost=10, adjusted_by=self.admin,
                reason=f'{marker}: receipt and physical stock count checked with the daily records.',
            )
        params = {
            'type': 'inventory', 'start_date': str(today.replace(day=1)),
            'end_date': str(today),
        }
        preview = self.client.get('/api/reports/preview/', params)
        self.assertEqual(preview.status_code, 200, preview.data)
        remarks = preview.data['data']['ledger']['rows'][0]['remarks']
        for marker in markers:
            self.assertIn(marker, remarks)

        class UncompressedFooterCanvas(FooterCanvas):
            def __init__(self, *args, **kwargs):
                kwargs['pageCompression'] = 0
                super().__init__(*args, **kwargs)

        # Inspect the actual PDF text streams without adding a PDF dependency.
        with patch('reporting.pdf.FooterCanvas', UncompressedFooterCanvas):
            response = self.client.get('/api/reports/export-pdf/', params)
            self.assertEqual(response.status_code, 200)
            content = b''.join(response.streaming_content)
        self.assertTrue(content.startswith(b'%PDF'))
        self.assertGreater(len(re.findall(rb'/Type\s*/Page\b', content)), 2)
        self.assertGreater(content.count(b'(Remarks)'), 1)
        for marker in markers:
            self.assertIn(marker.encode(), content)
