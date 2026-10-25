import json
from datetime import date
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from forecasting.beta_graph import PERIODS, SCOPES, refresh


class Command(BaseCommand):
    help = 'Refresh saved three-line real-sales graphs. Demo evaluation requires an explicit development-only scope.'

    def add_arguments(self, parser):
        parser.add_argument('--as-of', type=date.fromisoformat)
        parser.add_argument('--scope', choices=SCOPES)
        parser.add_argument('--period', choices=PERIODS)

    def handle(self, *args, **options):
        as_of = options['as_of'] or timezone.localdate()
        results = {}
        try:
            for scope in (options['scope'],) if options['scope'] else ('real',):
                for period in (options['period'],) if options['period'] else PERIODS:
                    run, created = refresh(period, scope, as_of)
                    results[f'{scope}:{period}'] = {'model_id': run.pk, 'created': created}
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps({'created': any(row['created'] for row in results.values()), 'graphs': results}))
