"""Back up and merge formatting duplicates and approved product aliases."""

import gzip
import hashlib
import io
import itertools
import json
import os
from pathlib import Path

from django.apps import apps
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from forecasting.refresh_jobs import maintenance_lock
from inventory.services.product_consolidation import consolidation_plan, consolidate_products


# Every column except the deliberately changed catalog references must match.
CHANGED_COLUMNS = {
    'inventory_productbatch': {'product_id'},
    'sales_orderitem': {'product_id'},
    'sales_transactionitem': {'product_id_snapshot', 'product_name_snapshot',
                              'category_id_snapshot', 'category_name_snapshot',
                              'source_product_label'},
}


def fingerprints(tables):
    result = {}
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        for table in tables:
            # Cache invalidation metadata intentionally changes during merges.
            if table in {'inventory_product', 'config_cacheinvalidation'}:
                continue
            excluded = CHANGED_COLUMNS.get(table, set())
            columns = [col.name for col in connection.introspection.get_table_description(cursor, table)
                       if col.name not in excluded]
            ordering = 'id' if 'id' in columns else ', '.join(quote(col) for col in columns)
            selected = ', '.join(quote(col) for col in columns)
            cursor.execute(f'SELECT row_to_json(t)::text FROM '
                           f'(SELECT {selected} FROM {quote(table)} ORDER BY {ordering}) t')
            digest, count = hashlib.sha256(), 0
            while rows := cursor.fetchmany(1000):
                for row in rows:
                    digest.update(row[0].encode())
                    digest.update(b'\n')
                    count += 1
            result[table] = {'count': count, 'sha256': digest.hexdigest()}
    return result


class Command(BaseCommand):
    help = 'Preview formatting duplicates and approved aliases; --apply backs up and merges atomically.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--product-id', type=int, action='append',
                            help='Restrict consolidation to these product IDs; repeat for each member.')

    def handle(self, *args, **options):
        product_ids = options['product_id']
        if not options['apply']:
            self.stdout.write(json.dumps(consolidation_plan(product_ids), indent=2))
            return
        if connection.vendor != 'postgresql':
            raise CommandError('Applying consolidation requires PostgreSQL table locks.')
        with maintenance_lock() as acquired:
            if not acquired:
                raise CommandError('Forecast maintenance is running. Retry consolidation after it finishes.')
            self.apply_consolidation(product_ids)

    def apply_consolidation(self, product_ids):
        with transaction.atomic():
            with connection.cursor() as cursor:
                tables = connection.introspection.table_names(cursor)
                cursor.execute("SET LOCAL lock_timeout = '10s'")
                cursor.execute('LOCK TABLE ' + ', '.join(connection.ops.quote_name(t) for t in tables)
                               + ' IN SHARE ROW EXCLUSIVE MODE')
            plan = consolidation_plan(product_ids)
            if not plan:
                self.stdout.write('No formatting duplicates or approved aliases found in the selected products.')
                return
            directory = Path('backups') / timezone.localtime().strftime('product-consolidation-%Y%m%d-%H%M%S-%f')
            directory.mkdir(mode=0o700, parents=True)
            backup = directory / 'database-before.json.gz'
            with backup.open('xb') as raw:
                os.chmod(backup, 0o600)
                with gzip.GzipFile(fileobj=raw, mode='wb') as compressed:
                    with io.TextIOWrapper(compressed, encoding='utf-8') as stream:
                        rows = itertools.chain.from_iterable(
                            model.objects.all().iterator(chunk_size=1000) for model in apps.get_models())
                        serializers.serialize('json', rows, stream=stream)
            with gzip.open(backup, 'rt', encoding='utf-8') as stream:
                backup_count = len(json.load(stream))
            before = fingerprints(tables)
            manifest = directory / 'manifest.json'
            audit = {'backup': str(backup), 'backup_records': backup_count,
                     'plan': plan, 'before': before, 'completed': False}
            manifest.write_text(json.dumps(audit, indent=2) + '\n')
            manifest.chmod(0o600)
            merged = consolidate_products(product_ids)
            after = fingerprints(tables)
            if before != after:
                raise CommandError('Unexpected data changes; all merges have been rolled back. See backup manifest.')
            if consolidation_plan(product_ids):
                raise CommandError('Duplicate records remain; rolling back.')
            audit.update(merged=merged, after=after)
            manifest.write_text(json.dumps(audit, indent=2) + '\n')
        audit['completed'] = True
        manifest.write_text(json.dumps(audit, indent=2) + '\n')
        self.stdout.write(self.style.SUCCESS(
            f'Merged {sum(len(p["members"])-1 for p in merged)} duplicate products in {len(merged)} groups. '
            f'Backup and audit: {directory}'))
