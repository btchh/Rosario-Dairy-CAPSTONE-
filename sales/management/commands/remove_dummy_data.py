"""Remove only the retired generator's namespace and explicit demo artifacts."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from config.data_cache import invalidate_data_cache
from forecasting.models import ForecastExperiment, GraphForecastRun, GraphPrediction
from sales.models import Order, OrderItem, StockReturn, Transaction, TransactionItem
from sales.backups.security import create_private_directory

DEMO_REFERENCE = r'^2026:[0-9]{6}$'


def targets():
    sales = Transaction.objects.filter(source_reference__regex=DEMO_REFERENCE)
    orders = Order.objects.filter(transaction__in=sales)
    demos = GraphForecastRun.objects.filter(scope='demo2026')
    return [
        OrderItem.objects.filter(order__in=orders),
        orders,
        TransactionItem.objects.filter(transaction__in=sales),
        sales,
        GraphPrediction.objects.filter(run__in=demos),
        demos,
        ForecastExperiment.objects.all(),
    ]


def fingerprint(query):
    digest = hashlib.sha256()
    for row in query.order_by('pk').values().iterator(chunk_size=1000):
        digest.update(json.dumps(row, sort_keys=True, default=str).encode()+b'\n')
    return digest.hexdigest()


def remove_dummy_data(folder):
    if connection.vendor != 'postgresql':
        raise CommandError('Apply requires PostgreSQL for transactional table locking.')
    create_private_directory(folder)
    manifest = {'completed': False, 'receipt_pattern': DEMO_REFERENCE}
    with transaction.atomic():
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
            cursor.execute("SET LOCAL lock_timeout = '10s'")
            cursor.execute('LOCK TABLE '+', '.join(connection.ops.quote_name(t) for t in sorted(tables))
                           +' IN SHARE ROW EXCLUSIVE MODE')
        queries = targets()
        selected = {q.model: list(q.values_list('pk', flat=True)) for q in queries}
        items = TransactionItem.objects.filter(pk__in=selected[TransactionItem])
        if items.filter(product_batch__is_historical_reference=False).exists():
            raise CommandError('Dummy receipts reference live stock; reconcile their stock movements before removal.')
        if StockReturn.objects.filter(sale_item__in=items).exists():
            raise CommandError('Dummy receipts have recorded stock returns; reconcile before removal.')
        manifest['removed'] = {model._meta.label: len(ids) for model, ids in selected.items()}
        # Derived invalidation records change as the selected rows are deleted.
        protected = {model: model._base_manager.exclude(pk__in=selected.get(model, []))
                     for model in apps.get_models()
                     if model._meta.label_lower != 'config.cacheinvalidation'}
        before = {model: fingerprint(query) for model, query in protected.items()}
        backup = folder/'database-before.json.gz'
        with backup.open('xb') as raw:
            os.chmod(backup, 0o600)
            with gzip.GzipFile(fileobj=raw, mode='wb') as compressed:
                with io.TextIOWrapper(compressed, encoding='utf-8') as output:
                    call_command('dumpdata', use_base_manager=True, all=True, stdout=output, verbosity=0)
        # Read and parse the entire stream before the first deletion.
        with gzip.open(backup, 'rt', encoding='utf-8') as source:
            records = json.load(source)
        if not isinstance(records, list):
            raise CommandError('Backup validation failed; no rows removed.')
        manifest.update(backup_records=len(records),
                        backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
                        protected_sha256={m._meta.label: digest for m, digest in before.items()})
        # Freeze IDs before deleting parents; dependent querysets would otherwise
        # become empty once their related orders or transactions disappear.
        for model, ids in selected.items():
            if ids:
                model._base_manager.filter(pk__in=ids).delete()
        for model, query in protected.items():
            if fingerprint(query) != before[model]:
                raise CommandError(f'Unrelated data changed: {model._meta.label}. Removal rolled back.')
        if any(query.exists() for query in targets()):
            raise CommandError('Dummy data remains. Removal rolled back.')
        manifest['protected_verified'] = True
        invalidate_data_cache()
    manifest['completed'] = True
    path = folder/'manifest.json'
    with path.open('x') as output:
        os.chmod(path, 0o600)
        json.dump(manifest, output, indent=2)
        output.write('\n')
    return manifest


class Command(BaseCommand):
    help = 'Preview dummy data removal; --apply makes a full private backup and removes only generated data.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--output', type=Path, help='New private recovery directory, required with --apply.')

    def handle(self, *args, **options):
        if not options['apply']:
            self.stdout.write(json.dumps({'applied': False,
                'targets': {q.model._meta.label: q.count() for q in targets()}}))
            return
        if options['output'] is None:
            raise CommandError('--output is required with --apply.')
        try:
            result = remove_dummy_data(options['output'])
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps({'applied': True, 'backup': str(options['output']), **result}))
