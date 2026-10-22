"""Preview or back up and atomically refresh the source workbook dataset."""
import csv
import gzip
import hashlib
import json
from pathlib import Path
import shutil

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from sales.models import Transaction, Order
from sales.importers.annual_sales import parse_workbooks
from sales.importers.refresh_annual_sales import POLICY_PATH, build_plan, apply_plan


def protected_fingerprints(references):
    """Preserve every existing catalog/stock/account row and all other sales."""
    historical_ids = list(Transaction.objects.filter(source_reference__in=references).values_list('pk', flat=True))
    orders = list(Order.objects.filter(transaction_id__in=historical_ids).values_list('pk', flat=True))
    protected = {}
    for model in apps.get_models():
        query = model._base_manager.order_by('pk')
        label = model._meta.label_lower
        if label == 'sales.transaction': query = query.exclude(pk__in=historical_ids)
        elif label == 'sales.transactionitem': query = query.exclude(transaction_id__in=historical_ids)
        elif label == 'sales.order': query = query.exclude(pk__in=orders)
        elif label == 'sales.orderitem': query = query.exclude(order_id__in=orders)
        ids = list(query.values_list('pk', flat=True))
        digest = hashlib.sha256()
        for row in query.values().iterator(chunk_size=1000):
            digest.update(json.dumps(row, sort_keys=True, default=str).encode() + b'\n')
        protected[label] = {'ids': ids, 'sha256': digest.hexdigest()}
    return protected


def verify_protected(before):
    for model in apps.get_models():
        expected = before[model._meta.label_lower]
        digest = hashlib.sha256()
        for row in model._base_manager.filter(pk__in=expected['ids']).order_by('pk').values().iterator(chunk_size=1000):
            digest.update(json.dumps(row, sort_keys=True, default=str).encode() + b'\n')
        if digest.hexdigest() != expected['sha256']:
            raise ValueError(f'Protected data changed: {model._meta.label}; rolling back.')


class Command(BaseCommand):
    help = 'Audit all three annual workbooks; --apply backs up and refreshes only their imported dairy history.'

    def add_arguments(self, parser):
        parser.add_argument('source_dir', type=Path)
        parser.add_argument('--output', type=Path, required=True, help='New private audit directory (must not exist).')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--admin-username')

    def handle(self, *args, **options):
        folder = options['output']
        try:
            if options['apply'] and not options['admin_username']:
                raise ValueError('--admin-username is required with --apply.')
            paths = sorted(options['source_dir'].glob('SALES REPORT JAN-DEC *.xlsx'))
            if len(paths) != 3:
                raise ValueError('Expected the three annual XLSX workbooks.')
            if {p.name for p in paths} != {f'SALES REPORT JAN-DEC {year}.xlsx' for year in (2023, 2024, 2025)}:
                raise ValueError('Expected the original 2023, 2024, and 2025 workbooks.')
            folder.mkdir(mode=0o700, parents=True, exist_ok=False)
            source = folder / 'sources'; source.mkdir(mode=0o700)
            manifest = {'completed': False, 'applied': False, 'files': {}}
            for path in [*paths, POLICY_PATH]:
                dest = source / path.name
                shutil.copyfile(path, dest); dest.chmod(0o600)
                digest = hashlib.sha256(dest.read_bytes()).hexdigest()
                if digest != hashlib.sha256(path.read_bytes()).hexdigest():
                    raise ValueError('Source changed during copy; retry with stable files.')
                manifest['files'][str(dest.relative_to(folder))] = digest
            parsed = parse_workbooks(source, separate_unlabelled_totals=True)
            policy = json.loads((source / POLICY_PATH.name).read_text())
            planned, audit = build_plan(parsed, policy)
            (folder / 'audit.json').write_text(json.dumps(audit, indent=2) + '\n')
            for key in ('monthly', 'retained', 'excluded_lines', 'review', 'held_dairy_lines'):
                rows = audit[key]
                if rows:
                    columns = list(dict.fromkeys(k for row in rows for k in row))
                    with (folder / f'{key}.csv').open('w', newline='') as stream:
                        writer = csv.DictWriter(stream, fieldnames=columns)
                        writer.writeheader(); writer.writerows(rows)
            if options['apply']:
                if connection.vendor != 'postgresql':
                    raise ValueError('Applying the refresh requires PostgreSQL table locks.')
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        tables = connection.introspection.table_names(cursor)
                        cursor.execute("SET LOCAL lock_timeout = '10s'")
                        cursor.execute('LOCK TABLE ' + ', '.join(connection.ops.quote_name(t) for t in tables)
                                       + ' IN SHARE ROW EXCLUSIVE MODE')
                    backup = folder / 'database-before.json.gz'
                    with gzip.open(backup, 'wt') as stream:
                        call_command('dumpdata', use_base_manager=True, all=True, stdout=stream, verbosity=0)
                    with gzip.open(backup, 'rt') as stream:
                        manifest['backup_records'] = len(json.load(stream))
                    manifest['files'][backup.name] = hashlib.sha256(backup.read_bytes()).hexdigest()
                    references = {t.reference for t in parsed.tickets}
                    protected = protected_fingerprints(references)
                    manifest['protected_before'] = protected
                    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
                    result = apply_plan(parsed, planned, options['admin_username'])
                    verify_protected(protected)
                    # Payment/void/handling fields on targeted receipts are also
                    # preserved by apply_plan; unrelated rows are fingerprinted.
                    manifest.update(result=result, protected_verified=True)
                manifest['applied'] = True
            manifest['completed'] = True
            for path in folder.iterdir():
                if path.is_file() and path.name != 'manifest.json':
                    path.chmod(0o600)
                    manifest['files'][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
            (folder / 'manifest.json').chmod(0o600)
            self.stdout.write(json.dumps({'audit': str(folder), 'applied': manifest['applied'],
                                         **audit['summary'], 'result': manifest.get('result')}, indent=2))
        except (ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc
