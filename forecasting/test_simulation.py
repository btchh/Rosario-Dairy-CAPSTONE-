from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from forecasting.data import source
from forecasting.models import SimulationRun
from forecasting.simulation import generate
from sales.models import Transaction


def history():
    feeding, other = {}, {}
    day = date(2023, 1, 1)
    while day <= date(2025, 12, 31):
        # Deliberate weekly zero days, monthly variation, and yearly growth.
        value = Decimal((day.month * 10 + day.year - 2020) if day.weekday() < 5 else 0)
        feeding[day], other[day] = value, value * 2
        day += timedelta(days=1)
    components = {'feeding': feeding, 'other_sales': other}
    daily = {day: feeding[day] + other[day] for day in feeding}
    return daily, 'unused', [], components


class GeneratorTests(SimpleTestCase):
    def test_calendar_repeatability_and_joint_weekday_pattern(self):
        rows, metadata = generate(history()[3])
        self.assertEqual((rows, metadata), generate(history()[3]))
        self.assertNotEqual(rows, generate(history()[3], seed=42)[0])
        self.assertEqual([row['date'] for row in rows],
                         [date(2026, 1, 1) + timedelta(days=i) for i in range(365)])
        for row in rows:
            self.assertGreaterEqual(row['feeding_revenue'], 0)
            self.assertLessEqual(abs(row['other_sales_revenue'] - row['feeding_revenue'] * 2), Decimal('.01'))
            if row['date'].weekday() >= 5:
                self.assertEqual(row['feeding_revenue'], 0)
        self.assertEqual(metadata['data_kind'], 'synthetic')

    def test_rejects_negative_history(self):
        components = history()[3]
        components['feeding'][date(2023, 2, 1)] = -1
        with self.assertRaises(ValueError):
            generate(components)


class SimulationCommandTests(TestCase):
    @patch('forecasting.management.commands.generate_sales_simulation.source', side_effect=lambda **kw: history())
    def test_atomic_idempotent_insert_exports_and_real_source_isolation(self, mock_source):
        original_source = source()
        original_count = Transaction.objects.count()
        with TemporaryDirectory() as directory:
            for _ in range(2):
                call_command('generate_sales_simulation', output=Path(directory), stdout=StringIO())
            csv = (Path(directory) / 'synthetic_2026.csv').read_text()
            self.assertEqual(len(csv.splitlines()), 366)
            self.assertIn('synthetic', csv)
        self.assertEqual(SimulationRun.objects.count(), 1)
        self.assertEqual(SimulationRun.objects.get().days.count(), 365)
        self.assertEqual(Transaction.objects.count(), original_count)
        self.assertEqual(source(), original_source)

    @patch('forecasting.management.commands.generate_sales_simulation.source')
    def test_incomplete_source_does_not_insert(self, mock_source):
        daily, signature, warnings, components = history()
        mock_source.return_value = ({day: value for day, value in daily.items() if day.month != 2},
                                    signature, warnings, components)
        with self.assertRaises(CommandError):
            call_command('generate_sales_simulation', stdout=StringIO())
        self.assertEqual(SimulationRun.objects.count(), 0)
