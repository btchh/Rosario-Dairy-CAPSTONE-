"""Run once from a scheduler, or repeatedly as new actual sales arrive."""
import csv
from datetime import date
import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from forecasting.jobs import refresh_patterns


class Command(BaseCommand):
    help='Refresh versioned regular-sales pattern studies; never fit or publish a prediction.'

    def add_arguments(self,parser):
        parser.add_argument('--as-of',type=date.fromisoformat)
        parser.add_argument('--output',type=Path)

    def handle(self,*args,**options):
        if options['as_of'] and options['as_of']>timezone.localdate():
            raise CommandError('as-of cannot be in the future')
        try:study,created=refresh_patterns(options['as_of'])
        except ValueError as exc:raise CommandError(str(exc)) from exc
        if options['output']:
            folder=options['output'];folder.mkdir(parents=True,exist_ok=True)
            (folder/'patterns.json').write_text(json.dumps(study.findings,indent=2)+'\n')
            for key in ('monthly','customers','weekday','days_of_month','year_over_year'):
                rows=study.findings[key]
                if rows:
                    with (folder/f'{key}.csv').open('w',newline='') as stream:
                        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
                        writer.writeheader();writer.writerows(rows)
            comparison=[{'benchmark':name,'validation_wape':values['validation']['wape_percent'],
                         'test_wape':values['test']['wape_percent']}
                        for name,values in study.findings['benchmarks'].get('benchmarks',{}).items()]
            if comparison:
                with (folder/'benchmarks.csv').open('w',newline='') as stream:
                    writer=csv.DictWriter(stream,fieldnames=list(comparison[0]))
                    writer.writeheader();writer.writerows(comparison)
        self.stdout.write(json.dumps({'study_id':study.pk,'created':created,'observed_through':str(study.observed_through),
                                     'target':study.findings['target'],
                                     'selected_benchmark_test':study.findings['benchmarks'].get('selected_test')}))
