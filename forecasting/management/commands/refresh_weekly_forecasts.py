import csv
from datetime import date
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from forecasting.weekly import refresh_weekly


class Command(BaseCommand):
    help = 'Fit/reuse actual-data weekly SARIMA research offline; never approve a production forecast.'

    def add_arguments(self, parser):
        parser.add_argument('--as-of', type=date.fromisoformat)
        parser.add_argument('--output', type=Path)

    def handle(self, *args, **options):
        try:
            run, created = refresh_weekly(options['as_of'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if options['output']:
            folder = options['output']; folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = folder/'model.json';path.write_text(json.dumps(run.findings, indent=2, allow_nan=False)+'\n');path.chmod(0o600)
            name = run.findings.get('selected_candidate')
            rows = run.findings.get('candidates', {}).get(name, {}).get('test_rows', [])
            if rows:
                path = folder/'weekly-backtest.csv'
                with path.open('w', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=['date', 'trained_through', 'actual', 'predicted'])
                    writer.writeheader();writer.writerows(rows)
                path.chmod(0o600)
        self.stdout.write(json.dumps({'model_id': run.pk, 'created': created, 'status': run.status,
            'selected_candidate': run.findings.get('selected_candidate'),
            'selected_test': run.findings.get('selected_test'), 'baseline_test': run.findings.get('baseline_test')}, allow_nan=False))
