"""Run and persist an isolated comparison of SARIMA-only sales models."""
import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from forecasting.data import as_series, source
from forecasting.models import ForecastRun
from forecasting.segmented_sarima import VERSION, evaluate, fingerprint


class Command(BaseCommand):
    help = 'Compare daily and segmented/calendar SARIMA on the same Jul-Dec 2025 retrospective test.'

    def add_arguments(self, parser):
        parser.add_argument('--workers', type=int, choices=(1, 2, 3, 4), default=2)
        parser.add_argument('--output', type=Path, default=Path('forecast_experiments/segmented_sarima'))

    def handle(self, *args, **options):
        def progress(message):
            self.stdout.write(message)
            self.stdout.flush()

        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [82197033])
            acquired = cursor.fetchone()[0]
        if not acquired:
            raise CommandError('Another forecast evaluation is running')
        try:
            daily, signature, _, components = source(include_components=True)
            config_signature = fingerprint()
            run = ForecastRun.objects.filter(scope='real', version=VERSION,
                configuration_signature=config_signature, source_signature=signature).first()
            if run is None:
                progress('Real sales only. Selection: Jan-Jun 2025. Retrospective test: Jul-Dec 2025.')
                folder = options['output']
                folder.mkdir(parents=True, exist_ok=True)
                checkpoint_path = folder / 'checkpoint.json'
                identity = {'source_signature': signature,
                            'configuration_signature': config_signature, 'version': VERSION}
                completed = {}
                if checkpoint_path.exists():
                    try:
                        saved = json.loads(checkpoint_path.read_text())
                        if all(saved.get(key) == value for key, value in identity.items()):
                            completed = saved.get('periods', {})
                    except (ValueError, OSError):
                        progress('Ignoring unreadable checkpoint')
                def checkpoint(periods):
                    temporary = folder / 'checkpoint.tmp'
                    temporary.write_text(json.dumps({**identity, 'periods': periods}, allow_nan=False) + '\n')
                    temporary.replace(checkpoint_path)
                try:
                    result = evaluate(as_series(daily),
                        {name: as_series(values) for name, values in components.items()},
                        workers=options['workers'], progress=progress,
                        completed=completed, checkpoint=checkpoint)
                except (ValueError, TypeError) as exc:
                    raise CommandError(str(exc)) from exc
                if source()[1] != signature:
                    raise CommandError('Real source changed during evaluation; retry')
                result['source_signature'] = signature
                run = ForecastRun.objects.create(scope='real', version=VERSION,
                    configuration_signature=config_signature, source_signature=signature,
                    data_end=max(daily), result=result)
            result = run.result
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [82197033])

        folder = options['output']
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'comparison.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
        summary = []
        predictions = []
        for period, evaluation in result['periods'].items():
            for approach, data in evaluation['approaches'].items():
                for segment, metric in {'total': data['metrics'], **data['component_metrics']}.items():
                    summary.append({'period': period, 'approach': approach, 'segment': segment,
                        'strategy': data['selection']['strategy'],
                        'validation_wape_percent': (data['selection']['validation_metrics'] if segment == 'total'
                            else data['selection']['component_validation_metrics'][segment])['wape_percent'],
                        'test_wape_percent': metric['wape_percent'], 'test_rows': metric['rows'],
                        'failed_rows': metric['failed_rows']})
                for segment, rows in {'total': data['rows'], **data['component_rows']}.items():
                    for row in rows:
                        predictions.append({'period': period, 'approach': approach,
                            'segment': segment, **row})
        for filename, rows in (('comparison.csv', summary), ('predictions.csv', predictions)):
            fields = list(dict.fromkeys(key for row in rows for key in row))
            with (folder / filename).open('w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        self.stdout.write(json.dumps({'run_id': run.pk, 'version': VERSION,
                                      'production_engine_changed': False, 'results': summary}))
