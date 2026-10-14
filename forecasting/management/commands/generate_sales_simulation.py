"""Insert an isolated, auditable daily 2026 scenario in PostgreSQL."""
import csv
import json
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from forecasting.data import source
from forecasting.models import SimulatedSalesDay, SimulationRun
from forecasting.simulation import METHOD, SEGMENTS, YEARS, generate


class Command(BaseCommand):
    help = 'Generate 365 simulated 2026 revenue days in separate tables; never insert sales transactions.'

    def add_arguments(self, parser):
        parser.add_argument('--seed', type=int, default=2026)
        parser.add_argument('--output', type=Path, help='Optional private CSV and metadata export directory.')

    def handle(self, *args, **options):
        seed = options['seed']
        if not 0 <= seed <= 2147483647:
            raise CommandError('Seed must be between 0 and 2147483647')
        daily, _, _, all_components = source(include_components=True)
        # Hash only the eligible historical source, independent of live 2026 sales.
        components = {segment: {day: amount for day, amount in all_components[segment].items()
                                if day.year in YEARS} for segment in SEGMENTS}
        from hashlib import sha256
        signature = sha256(json.dumps({segment: [(str(day), str(amount))
            for day, amount in sorted(values.items())] for segment, values in components.items()},
            sort_keys=True).encode()).hexdigest()
        for year in YEARS:
            for month in range(1, 13):
                if not any(day.year == year and day.month == month for day in daily):
                    raise CommandError(f'No recorded sales in {year}-{month:02}; verify source coverage')
        try:
            rows, metadata = generate(components, seed)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        with transaction.atomic():
            run, created = SimulationRun.objects.get_or_create(
                target_year=2026, seed=seed, method=METHOD, source_signature=signature,
                defaults={'metadata': metadata})
            if created:
                SimulatedSalesDay.objects.bulk_create([SimulatedSalesDay(run=run, **row) for row in rows])
            persisted = list(run.days.values('date', 'feeding_revenue', 'other_sales_revenue'))
            if len(persisted) != 365 or persisted[0]['date'] != date(2026, 1, 1) or persisted[-1]['date'] != date(2026, 12, 31):
                raise CommandError('Stored simulation has incomplete coverage')
        folder = options['output']
        if folder:
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / 'synthetic_2026.csv').open('w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=['run_id', 'data_kind', 'date',
                    'feeding_revenue', 'other_sales_revenue', 'total_revenue'])
                writer.writeheader()
                for row in persisted:
                    writer.writerow({'run_id': run.pk, 'data_kind': 'synthetic', **row,
                        'total_revenue': row['feeding_revenue'] + row['other_sales_revenue']})
            (folder / 'metadata.json').write_text(json.dumps({
                'run_id': run.pk, 'seed': seed, 'method': METHOD,
                'source_signature': signature, **run.metadata}, indent=2) + '\n')
        total = sum(row['feeding_revenue'] + row['other_sales_revenue'] for row in persisted)
        self.stdout.write(json.dumps({'run_id': run.pk, 'created': created, 'rows': len(persisted),
            'start': '2026-01-01', 'end': '2026-12-31', 'total_revenue': str(total),
            'applied_growth': run.metadata['applied_growth'], 'data_kind': 'synthetic'}))
