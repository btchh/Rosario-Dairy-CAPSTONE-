"""Preview and back up clear customer identity merges before changing links."""
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
from sales.services.customer_consolidation import consolidation_plan, consolidate_customers


def fingerprints(tables):
    result = {}
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        for table in tables:
            # Cache invalidation metadata intentionally changes during merges.
            if table in {'sales_customer', 'config_cacheinvalidation'}:
                continue
            excluded = {'customer_id'} if table in ('sales_transaction', 'sales_order') else set()
            columns = [c.name for c in connection.introspection.get_table_description(cursor, table) if c.name not in excluded]
            selected = ', '.join(quote(c) for c in columns)
            ordering = quote('id') if 'id' in columns else selected
            cursor.execute(f'SELECT row_to_json(t)::text FROM (SELECT {selected} FROM {quote(table)} ORDER BY {ordering}) t')
            digest, count = hashlib.sha256(), 0
            while rows := cursor.fetchmany(1000):
                for row in rows:
                    digest.update(row[0].encode()); digest.update(b'\n'); count += 1
            result[table] = {'count': count, 'sha256': digest.hexdigest()}
    return result


class Command(BaseCommand):
    help = 'Preview clear customer aliases; --apply backs up and consolidates them atomically.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        if not options['apply']:
            self.stdout.write(json.dumps(consolidation_plan(), indent=2))
            return
        with maintenance_lock() as acquired:
            if not acquired:
                raise CommandError('Forecast maintenance is running. Retry consolidation after it finishes.')
            with transaction.atomic():
                with connection.cursor() as cursor:
                    tables = connection.introspection.table_names(cursor)
                    cursor.execute("SET LOCAL lock_timeout = '10s'")
                    cursor.execute('LOCK TABLE ' + ', '.join(connection.ops.quote_name(t) for t in tables)
                                   + ' IN SHARE ROW EXCLUSIVE MODE')
                plan = consolidation_plan()
                if not plan['merge_groups']:
                    self.stdout.write('No clear customer duplicates found. Ambiguous identities are retained for review.')
                    return
                folder = Path('backups')/timezone.localtime().strftime('customer-consolidation-%Y%m%d-%H%M%S-%f')
                folder.mkdir(parents=True, mode=0o700)
                path = folder/'database-before.json.gz'
                with path.open('xb') as raw:
                    os.chmod(path, 0o600)
                    with gzip.GzipFile(fileobj=raw, mode='wb') as compressed:
                        with io.TextIOWrapper(compressed, encoding='utf-8') as stream:
                            rows = itertools.chain.from_iterable(model.objects.all().iterator(chunk_size=1000)
                                                                  for model in apps.get_models())
                            serializers.serialize('json', rows, stream=stream)
                with gzip.open(path, 'rt') as stream:
                    backup_count = len(json.load(stream))
                before = fingerprints(tables)
                from sales.models import Customer
                customer_count = Customer.objects.count()
                audit = {'completed': False, 'backup': str(path), 'backup_records': backup_count,
                         'customers_before': customer_count, 'plan': plan, 'before': before}
                manifest = folder/'manifest.json'
                manifest.write_text(json.dumps(audit, indent=2)+'\n'); manifest.chmod(0o600)
                merged = consolidate_customers(plan)
                after = fingerprints(tables)
                removed = sum(len(g['members'])-1 for g in plan['merge_groups'])
                if before != after or Customer.objects.count() != customer_count-removed:
                    raise CommandError('Unexpected data changes; consolidation rolled back. See private backup.')
                if consolidation_plan()['merge_groups']:
                    raise CommandError('Clear duplicates remain; consolidation rolled back.')
                audit.update(plan=merged, after=after, customers_after=Customer.objects.count())
                manifest.write_text(json.dumps(audit, indent=2)+'\n')
            audit['completed'] = True
            manifest.write_text(json.dumps(audit, indent=2)+'\n')
            self.stdout.write(f'Consolidated {removed} duplicate customers in {len(plan["merge_groups"])} groups. '
                              f'Backup and audit: {folder}')
