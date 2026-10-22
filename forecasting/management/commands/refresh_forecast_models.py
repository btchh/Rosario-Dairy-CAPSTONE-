import csv
from datetime import date
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from forecasting.lifecycle import refresh_models


class Command(BaseCommand):
    help = 'Fit/reuse monthly dairy models, score immutable issues, and apply quality-gated promotion offline.'

    def add_arguments(self, parser):
        parser.add_argument('--as-of', type=date.fromisoformat)
        parser.add_argument('--output', type=Path)

    def handle(self, *args, **options):
        try:
            run, result = refresh_models(options['as_of'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if options['output']:
            folder = options['output']; folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            export = {**run.findings, 'model_id': run.pk, 'maintenance': result}
            path = folder / 'model.json'
            path.write_text(json.dumps(export, indent=2, allow_nan=False) + '\n'); path.chmod(0o600)
            comparisons = []
            for name, values in run.findings.get('candidates', {}).items():
                comparisons.append({'candidate': name,
                    'role': values.get('role', 'primary' if name.startswith('sarima_') else 'comparison'),
                    'selected': name == run.findings.get('selected_candidate'),
                    'validation_wape': values.get('validation', {}).get('wape_percent'),
                    'test_wape': values.get('test', {}).get('wape_percent'),
                    'failure': values.get('reason') or values.get('test_failed', '')})
            if comparisons:
                path = folder / 'candidates.csv'
                with path.open('w', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(comparisons[0]))
                    writer.writeheader(); writer.writerows(comparisons)
                path.chmod(0o600)
            selected = run.findings.get('selected_candidate')
            for phase in ('validation', 'test'):
                rows = run.findings.get('candidates', {}).get(selected, {}).get(phase + '_rows', [])
                if rows:
                    path = folder / f'{phase}.csv'
                    with path.open('w', newline='') as stream:
                        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                        writer.writeheader(); writer.writerows(rows)
                    path.chmod(0o600)
        self.stdout.write(json.dumps({'model_id': run.pk, 'status': run.status,
            'selected_candidate': run.findings.get('selected_candidate'),
            'selected_test': run.findings.get('selected_test'), 'trained_through': str(run.trained_through),
            **result}, allow_nan=False))
