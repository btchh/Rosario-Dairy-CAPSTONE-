"""Scheduled learning: fit only on changed completed months or corrections."""
import time
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError


class Command(BaseCommand):
    help = 'Maintain monthly model versions, shadow predictions and actual-error scores as data grows.'

    def add_arguments(self, parser):
        parser.add_argument('--interval', type=int, default=3600)
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        if options['interval'] < 60:
            raise CommandError('interval must be at least 60 seconds')
        try:
            while True:
                try:
                    call_command('refresh_forecast_patterns', stdout=self.stdout)
                    call_command('refresh_forecast_models', stdout=self.stdout)
                except (CommandError, ValueError, DatabaseError) as exc:
                    if options['once']:
                        raise CommandError(str(exc)) from exc
                    self.stderr.write(f'Model maintenance failed; retrying next interval: {exc}')
                if options['once']:
                    break
                time.sleep(options['interval'])
        except KeyboardInterrupt:
            self.stdout.write('Model maintenance stopped.')
