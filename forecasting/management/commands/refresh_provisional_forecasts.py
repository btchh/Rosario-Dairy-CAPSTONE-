import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from forecasting.provisional import refresh


class Command(BaseCommand):
    help = 'Refresh rough actual-data SARIMA projections for all three views and horizons offline.'

    def add_arguments(self, parser):
        parser.add_argument('--output', type=Path)

    def handle(self, *args, **options):
        try:
            run, created = refresh()
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if options['output']:
            folder = options['output']
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = folder/'projections.json'
            path.write_text(json.dumps(run.findings, indent=2, allow_nan=False)+'\n')
            path.chmod(0o600)
        counts = {p: {v: len(result['points']) for v, result in views.items()}
                  for p, views in run.findings['periods'].items()}
        self.stdout.write(json.dumps({'model_id': run.pk, 'created': created, 'point_counts': counts,
                                     'production_approved': False}))
