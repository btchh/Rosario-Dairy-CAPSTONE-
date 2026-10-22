import json
from django.core.management.base import BaseCommand, CommandError
from forecasting.refresh_jobs import run_maintenance


class Command(BaseCommand):
    help = 'Run one tracked forecast maintenance job with the global manual/midnight lock.'

    def add_arguments(self, parser):
        parser.add_argument('--job-id', type=int)

    def handle(self, *args, **options):
        result = run_maintenance(options['job_id'])
        self.stdout.write(json.dumps(result))
        if result['status'] == 'failed':
            raise CommandError('Forecast refresh failed; inspect job status and worker log.')
