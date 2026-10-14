"""Compare recorded regular-sales forecasts with and without customer grouping."""
import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from forecasting.customer_sarima import VERSION, configuration_signature, evaluate, read_source
from forecasting.models import ForecastRun
from forecasting.segmented_sarima import VERSION as BASELINE_VERSION


class Command(BaseCommand):
    help = 'Test customer-based SARIMA on trusted purchase records; no churn assumptions or data edits.'

    def add_arguments(self, parser):
        parser.add_argument('--workers', type=int, choices=(1, 2, 3, 4), default=4)
        parser.add_argument('--output', type=Path, default=Path('forecast_experiments/customer_sarima'))

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
            daily, components, customers, signature, real_signature = read_source()
            previous = ForecastRun.objects.filter(scope='real', version=BASELINE_VERSION,
                                                  source_signature=real_signature).first()
            if previous is None:
                raise CommandError('Run compare_segmented_sarima on the current real source first.')
            config = configuration_signature()
            identity = {'source_signature': signature, 'configuration_signature': config, 'baseline_run': previous.pk}
            run = ForecastRun.objects.filter(scope='real', version=VERSION,
                configuration_signature=config, source_signature=signature).first()
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
                try:
                    result = evaluate(daily, components, customers, previous.result,
                        workers=options['workers'], cache_dir=folder / 'candidates', progress=progress,
                        completed=completed, checkpoint=checkpoint)
                except (ValueError, TypeError) as exc:
                    raise CommandError(str(exc)) from exc
                if read_source()[3] != signature:
                    raise CommandError('Customer assignments or real sales changed during evaluation; retry')
                result.update(real_source_signature=real_signature, baseline_run=previous.pk)
                run = ForecastRun.objects.create(scope='real', version=VERSION,
                    configuration_signature=config, source_signature=signature, data_end=daily.index.max().date(), result=result)
            result = run.result
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [82197033])
        (folder / 'results.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
        summary, predictions, customer_metrics = [], [], []
        for period, evaluation in result['periods'].items():
            for approach, data in evaluation['approaches'].items():
                for segment, metric in data['metrics'].items():
                    summary.append({'period': period, 'approach': approach, 'segment': segment,
                        'validation_selected': approach == evaluation['selected_approach'],
                        'wape_percent': metric['wape_percent'], 'mae_pesos': metric['mae_pesos'],
                        'test_rows': metric['rows'], 'failed_rows': metric['failed_rows']})
                predictions.extend({'period': period, 'approach': approach, **row} for row in data['rows'])
            customer_metrics.extend({'period': period, 'series': name, **metric}
                                    for name, metric in evaluation['customer_metrics'].items())
        for filename, rows in (('summary.csv', summary), ('predictions.csv', predictions),
                               ('customer_selection.csv', result['customer_selection']), ('customer_metrics.csv', customer_metrics)):
            fields = list(dict.fromkeys(key for row in rows for key in row))
            with (folder / filename).open('w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        self.stdout.write(json.dumps({'run_id': run.pk, 'version': VERSION, 'results': summary}))
