"""Run a bounded SARIMA search against real sales, preserving all earlier audits."""
import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from forecasting.data import as_series, source
from forecasting.models import ForecastRun
from forecasting.segmented_sarima import VERSION as PREVIOUS_VERSION
from forecasting.tuned_sarima import VERSION, configuration_signature, evaluate


class Command(BaseCommand):
    help = 'Tune real-sales SARIMA for below 25% WAPE, with 20% as the stretch target.'

    def add_arguments(self, parser):
        parser.add_argument('--workers', type=int, choices=(1, 2, 3, 4), default=4)
        parser.add_argument('--output', type=Path, default=Path('forecast_experiments/tuned_sarima'))

    def handle(self, *args, **options):
        def progress(message):
            self.stdout.write(message)
            self.stdout.flush()
        folder = options['output']
        folder.mkdir(parents=True, exist_ok=True)
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [82197033])
            acquired = cursor.fetchone()[0]
        if not acquired:
            raise CommandError('Another forecast evaluation is running')
        try:
            daily, signature, _, components = source(include_components=True)
            previous = ForecastRun.objects.filter(scope='real', version=PREVIOUS_VERSION,
                                                  source_signature=signature).first()
            if previous is None:
                raise CommandError('Run compare_segmented_sarima on the current real source first.')
            config = configuration_signature()
            run = ForecastRun.objects.filter(scope='real', version=VERSION,
                configuration_signature=config, source_signature=signature).first()
            identity = {'source_signature': signature, 'configuration_signature': config,
                        'version': VERSION, 'comparison_run': previous.pk}
            if run is None:
                completed = {}
                checkpoint_path = folder / 'checkpoint.json'
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
                progress('Real data only; validation Jan-Jun 2025; retrospective test Jul-Dec 2025; target <25%, stretch <20%.')
                try:
                    result = evaluate(as_series(daily), {name: as_series(values) for name, values in components.items()},
                        previous.result, workers=options['workers'], cache_dir=folder / 'candidates',
                        progress=progress, completed=completed, checkpoint=checkpoint)
                except (ValueError, TypeError) as exc:
                    raise CommandError(str(exc)) from exc
                if source()[1] != signature:
                    raise CommandError('Real source changed during tuning; retry')
                result.update(source_signature=signature, comparison_run=previous.pk)
                run = ForecastRun.objects.create(scope='real', version=VERSION,
                    configuration_signature=config, source_signature=signature, data_end=max(daily), result=result)
            result = run.result
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [82197033])
        (folder / 'results.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
        summary, predictions = [], []
        for period, evaluation in result['periods'].items():
            for name, metric in {'total': evaluation['metrics'], **evaluation['component_metrics']}.items():
                summary.append({'period': period, 'segment': name, 'wape_percent': metric['wape_percent'],
                    'test_rows': metric['rows'], 'failed_rows': metric['failed_rows'],
                    'strategy': evaluation['selection']['strategy'], 'total_status': evaluation['status']})
            for name, metric in evaluation['individual_component_metrics'].items():
                summary.append({'period': period, 'segment': f'{name}_independent',
                    'wape_percent': metric['wape_percent'], 'test_rows': metric['rows'],
                    'failed_rows': metric['failed_rows'], 'strategy': 'individual_diagnostic',
                    'total_status': evaluation['status']})
            for segment, rows in {'total': evaluation['rows'], **evaluation['component_rows']}.items():
                predictions.extend({'period': period, 'segment': segment, **row} for row in rows)
        for filename, rows in (('summary.csv', summary), ('predictions.csv', predictions)):
            fields = list(dict.fromkeys(key for row in rows for key in row))
            with (folder / filename).open('w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        self.stdout.write(json.dumps({'run_id': run.pk, 'version': VERSION, 'results': summary}))
