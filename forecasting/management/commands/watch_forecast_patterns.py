"""Optional long-running pattern refresh; production fitting is a later stage."""
import time
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help='Refresh research from new completed sales repeatedly; no forecast training or publication.'

    def add_arguments(self,parser):
        parser.add_argument('--interval',type=int,default=3600)
        parser.add_argument('--once',action='store_true')

    def handle(self,*args,**options):
        if options['interval']<60:raise CommandError('interval must be at least 60 seconds')
        try:
            while True:
                call_command('refresh_forecast_patterns',stdout=self.stdout)
                if options['once']:break
                time.sleep(options['interval'])
        except KeyboardInterrupt:
            self.stdout.write('Pattern refresh stopped.')
