"""Scheduled learning: fit only on changed completed months or corrections."""
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError


def seconds_until_midnight(now=None):
    """Use the business timezone even on a server configured in UTC."""
    now = (now or datetime.now(ZoneInfo('Asia/Manila'))).astimezone(ZoneInfo('Asia/Manila'))
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight-now).total_seconds()


class Command(BaseCommand):
    help = 'Maintain monthly production gates and separate weekly SARIMA research as actual data grows.'

    def add_arguments(self, parser):
        parser.add_argument('--interval', type=int, help='Explicit interval override in seconds; default is midnight Asia/Manila.')
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        if options['interval'] is not None and options['interval'] < 60:
            raise CommandError('interval must be at least 60 seconds')
        try:
            while True:
                if not options['once'] and options['interval'] is None:
                    time.sleep(seconds_until_midnight())
                try:
                    call_command('run_forecast_refresh', stdout=self.stdout)
                except (CommandError, ValueError, DatabaseError) as exc:
                    if options['once']:
                        raise CommandError(str(exc)) from exc
                    self.stderr.write(f'Model maintenance failed; retrying next scheduled run: {exc}')
                if options['once']:
                    break
                if options['interval'] is not None:
                    time.sleep(options['interval'])
        except KeyboardInterrupt:
            self.stdout.write('Model maintenance stopped.')
