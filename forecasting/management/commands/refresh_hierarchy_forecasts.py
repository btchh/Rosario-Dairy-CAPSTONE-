import json
from django.core.management.base import BaseCommand, CommandError
from forecasting.hierarchy import PERIODS, refresh
from forecasting.hierarchy_source import load_source
from forecasting.refresh_jobs import maintenance_lock


class Command(BaseCommand):
    help = 'Prepare saved item demand/revenue forecasts and coherent category/unit totals.'

    def add_arguments(self, parser):
        parser.add_argument('--period', choices=list(PERIODS))

    def handle(self, *args, **options):
        with maintenance_lock() as acquired:
            if not acquired:
                raise CommandError('Forecast maintenance is already running.')
            source = load_source()
            if source['latest_day'] is None:
                self.stdout.write(json.dumps({'status': 'no_history', 'created': False, 'runs': {}}))
                return
            results = {}
            for period in [options['period']] if options['period'] else PERIODS:
                run, created = refresh(period, source)
                results[period] = {'run_id': run.pk, 'created': created}
            self.stdout.write(json.dumps({'status': 'ready', 'created': any(r['created'] for r in results.values()), 'runs': results}))
