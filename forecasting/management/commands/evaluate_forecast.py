"""Run the offline SARIMA backtest and optionally export its audit."""
import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from forecasting.contract import LIMITATIONS, TARGET_PERCENT
from forecasting.jobs import refresh


class Command(BaseCommand):
    help = 'Validate SARIMA on 2024, test on 2025, and refresh the forecast snapshot.'

    def add_arguments(self, parser):
        parser.add_argument('--scope', choices=['real'], default='real')
        parser.add_argument('--output', type=Path, help='Optional private directory for backtest exports.')

    def handle(self, *args, **values):
        def progress(message):
            self.stdout.write(message)
            self.stdout.flush()

        try:
            run, updated = refresh(values['scope'], progress=progress)
        except (ValueError, TypeError) as exc:
            raise CommandError(str(exc)) from exc
        result = run.result
        setup = result['setups'][result['selected_setup']]
        summary = []
        for period, evaluation in setup['periods'].items():
            metric = evaluation['metrics']['combined']
            summary.append({'period':period, 'method':evaluation['method'],
                            'validation_wape_percent':(evaluation.get('validation_metrics') or {}).get('wape_percent'),
                            'holdout_wape_percent':metric['wape_percent'],
                            'holdout_rows':metric['rows'], 'status':evaluation['status']})
        self.stdout.write(json.dumps({'scope':values['scope'], 'updated':updated,
                                      'selected_setup':result['selected_setup'], 'periods':summary}))
        folder = values['output']
        if folder is None:
            return
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'real.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
        with (folder / 'comparison.csv').open('w', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=list(summary[0]))
            writer.writeheader()
            writer.writerows(summary)
        (folder / 'protocol.json').write_text(json.dumps({
            'model':'SARIMA', 'source':'completed non-voided transactions',
            'selection':'Every validation target ends before 2025. Models and component strategy are frozen before scoring 2025.',
            'test':'Retrospective one-period-ahead 2025 evaluation; these data were previously inspected.',
            'acceptance':f'At least five holdout periods and WAPE no higher than {TARGET_PERCENT}%',
            'limitations':LIMITATIONS,
        }, indent=2) + '\n')
