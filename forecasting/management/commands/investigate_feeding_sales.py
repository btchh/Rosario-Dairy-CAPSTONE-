"""Export actual feeding evidence without model fitting or synthetic inputs."""
import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from forecasting.feeding import feeding_report


class Command(BaseCommand):
    help = 'Export recorded actual feeding history and gaps; no model fitting.'

    def add_arguments(self, parser):
        parser.add_argument('--output', type=Path, required=True)

    def handle(self, *args, **options):
        report = feeding_report()
        folder = options['output']
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        report_path = folder / 'feeding-history.json'
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        report_path.chmod(0o600)
        csv_path = folder / 'feeding-months.csv'
        with csv_path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['month', 'invoice_count',
                'recorded_revenue', 'status', 'absence_of_demand_verified'])
            writer.writeheader()
            writer.writerows(report['months'])
        csv_path.chmod(0o600)
        self.stdout.write(json.dumps({'scope': 'real', 'invoice_count': report['invoice_count'],
            'source_revision': report['source_revision'], 'output': str(folder)}))
