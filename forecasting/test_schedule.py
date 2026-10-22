from datetime import datetime, timezone
from io import StringIO
from unittest.mock import call, patch
from zoneinfo import ZoneInfo

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase
from .management.commands.watch_forecast_models import seconds_until_midnight


class MidnightMaintenanceTests(SimpleTestCase):
    def test_next_run_uses_manila_midnight_on_a_utc_server(self):
        self.assertEqual(seconds_until_midnight(datetime(2026, 10, 22, 15, 59, tzinfo=timezone.utc)), 60)
        self.assertEqual(seconds_until_midnight(datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)), 86400)
        self.assertEqual(seconds_until_midnight(datetime(2026, 12, 31, 23, 59, 59,
                                                     tzinfo=ZoneInfo('Asia/Manila'))), 1)

    def test_default_worker_waits_then_recalculates_midnight_after_work(self):
        namespace = 'forecasting.management.commands.watch_forecast_models.'
        with patch(namespace+'seconds_until_midnight', side_effect=[60, 86355]), \
                patch(namespace+'time.sleep', side_effect=[None, KeyboardInterrupt]) as sleep, \
                patch(namespace+'call_command') as maintenance:
            call_command('watch_forecast_models', stdout=StringIO())
        self.assertEqual(sleep.call_args_list, [call(60), call(86355)])
        self.assertEqual([c.args[0] for c in maintenance.call_args_list], ['run_forecast_refresh'])

    def test_timer_once_run_is_immediate_and_never_sleeps(self):
        namespace = 'forecasting.management.commands.watch_forecast_models.'
        with patch(namespace+'time.sleep', side_effect=AssertionError('once must not sleep')), \
                patch(namespace+'call_command') as maintenance:
            call_command('watch_forecast_models', once=True, stdout=StringIO())
        self.assertEqual(maintenance.call_count, 1)

    def test_failed_run_waits_until_next_midnight_instead_of_looping(self):
        namespace = 'forecasting.management.commands.watch_forecast_models.'
        with patch(namespace+'seconds_until_midnight', side_effect=[10, 86390]), \
                patch(namespace+'time.sleep', side_effect=[None, KeyboardInterrupt]) as sleep, \
                patch(namespace+'call_command', side_effect=ValueError('source changed')):
            error = StringIO()
            call_command('watch_forecast_models', stdout=StringIO(), stderr=error)
        self.assertEqual(sleep.call_args_list, [call(10), call(86390)])
        self.assertIn('next scheduled run', error.getvalue())
        with self.assertRaises(CommandError):
            call_command('watch_forecast_models', once=True, interval=1)
