"""Scheduled learning: fit only on changed completed months or corrections."""
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.conf import settings


def seconds_until_midnight(now=None):
    """Use the business timezone even on a server configured in UTC."""
    business_zone = ZoneInfo(settings.TIME_ZONE)
    now = (now or datetime.now(business_zone)).astimezone(business_zone)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight-now).total_seconds()


class Command(BaseCommand):
    help = 'Refresh saved forecasts on startup, then at midnight Asia/Manila (or an explicit interval).'

    def add_arguments(self, parser):
        parser.add_argument('--interval', type=int, help='Explicit interval override in seconds; default is midnight Asia/Manila.')
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        if options['interval'] is not None and options['interval'] < 60:
            raise CommandError('interval must be at least 60 seconds')
        first_run = True
        try:
            while True:
                # Recover missed maintenance after downtime before waiting for
                # the next midnight. Unchanged inputs reuse saved model fits.
                if not first_run and not options['once'] and options['interval'] is None:
                    time.sleep(seconds_until_midnight())
                first_run = False
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
