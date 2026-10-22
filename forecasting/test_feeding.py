from datetime import date
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from .data import RecordedSale, SalesSnapshot
from .feeding import investigate_feeding, program_group


def snapshot():
    def sale(pk, day, name, segment='feeding'):
        return RecordedSale(pk, date.fromisoformat(day), f'customer:{pk}', name, segment, Decimal('100'))
    return SalesSnapshot(date(2026, 10, 1), 'revision', (
        sale(1, '2024-11-30', 'Milk Feeding-Las Piñas'),
        sale(2, '2024-12-01', 'MILK FEEDING LAS PINAS'),
        sale(3, '2025-02-02', 'MILK FEEDIN-DSWD'),
        sale(4, '2025-02-03', 'Milk Feeding WEEK 3'),
        sale(5, '2025-03-01', 'Unknown', 'unclassified'),
        sale(6, '2023-01-01', 'Walk-in', 'regular'),
    ), 10)


class FeedingDiagnosticsTests(SimpleTestCase):
    def test_gaps_do_not_become_verified_zero_demand_or_program_start(self):
        result = investigate_feeding(snapshot())
        self.assertEqual(result['invoice_count'], 4)
        self.assertEqual(result['first_recorded_date'], '2024-11-30')
        self.assertEqual(result['months_with_invoices'], 3)
        self.assertEqual(result['observed_calendar_months'], 4)
        self.assertEqual(result['months_without_invoices_within_span'], ['2025-01-01'])
        self.assertEqual(result['months'][2]['recorded_revenue'], 0)
        self.assertFalse(result['months'][2]['absence_of_demand_verified'])
        self.assertEqual(result['complete_months_since_last_record'], 19)
        self.assertFalse(result['readiness']['production_ready'])
        self.assertFalse(result['readiness']['history_review_threshold_met'])

    def test_display_groups_preserve_aliases_and_unidentified_programs(self):
        groups = {g['label']: g for g in investigate_feeding(snapshot())['program_groups']}
        self.assertEqual(groups['Las Piñas']['invoice_count'], 2)
        self.assertEqual(len(groups['Las Piñas']['source_labels']), 2)
        self.assertEqual(groups['Unspecified feeding program']['invoice_count'], 1)
        self.assertEqual(program_group('MILK FEEDING-DEPED'), 'DepEd')
        self.assertEqual(program_group('Milk Feeding-Rizal'), 'Rizal')
        self.assertEqual(program_group('DSWDish'), 'Unspecified feeding program')

    def test_empty_history_is_not_ready(self):
        empty = SalesSnapshot(date(2026, 1, 1), 'empty', (), 0)
        result = investigate_feeding(empty)
        self.assertEqual(result['status'], 'no_feeding_records')
        self.assertIsNone(result['first_recorded_date'])
        self.assertIsNone(result['complete_months_since_last_record'])
        self.assertEqual(result['recorded_revenue'], 0)

    def test_actual_history_export_contains_no_forecast_or_synthetic_metrics(self):
        with TemporaryDirectory() as folder, patch('forecasting.feeding.load_snapshot', return_value=snapshot()), \
                patch('forecasting.learning.train', side_effect=AssertionError('must not fit')):
            call_command('investigate_feeding_sales', output=Path(folder), stdout=StringIO())
            report = json.loads((Path(folder)/'feeding-history.json').read_text())
            self.assertEqual(report['scope'], 'real')
            self.assertEqual(report['invoice_count'], 4)
            self.assertNotIn('forecast', report)
            self.assertIn('no_feeding_invoice', (Path(folder)/'feeding-months.csv').read_text())


class FeedingEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_admin_reads_completed_actual_months_without_fitting(self):
        admin = get_user_model().objects.create_user(username='feeding-admin', email='feeding-admin@example.test', role='admin')
        self.client.force_authenticate(admin)
        with patch('forecasting.feeding.load_snapshot', return_value=snapshot()) as load, \
                patch('forecasting.learning.train', side_effect=AssertionError('must not fit')):
            response = self.client.get('/api/forecasting/feeding/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['scope'], 'real')
        self.assertEqual(response.data['source_revision'], 'revision')
        self.assertEqual(load.call_args.args[0].day, 1)

    def test_staff_and_anonymous_cannot_access(self):
        staff = get_user_model().objects.create_user(username='feeding-staff', email='feeding-staff@example.test', role='staff')
        for user, status in ((staff, 403), (None, 401)):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get('/api/forecasting/feeding/').status_code, status)
