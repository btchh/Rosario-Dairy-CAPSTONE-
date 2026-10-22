"""Data-only SQL export with FK dependencies and isolated restore verification.

Does not need pg_dump. Restore targets must have the current empty application
schema; this is a dated sales dataset backup, not a server/roles/schema dump.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import gzip
import json
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.apps import apps
from django.conf import settings
from django.db import connection, transaction
from django.db.backends.postgresql.base import DatabaseWrapper
from django.db.models import Q
from django.utils import timezone
from psycopg2 import sql

from sales.models import Customer, Order, OrderItem, Transaction, TransactionItem
from inventory.models import Product, Category


def select_rows(start_year, end_year):
    start = datetime(start_year, 1, 1, tzinfo=ZoneInfo('Asia/Manila'))
    end = datetime(end_year+1, 1, 1, tzinfo=ZoneInfo('Asia/Manila'))
    receipts = Transaction.objects.filter(created_at__gte=start, created_at__lt=end).exclude(
        source_reference__regex=r'^2026:[0-9]{6}$')
    ids = list(receipts.values_list('pk', flat=True))
    orders = Order.objects.filter(Q(transaction_id__in=ids) | Q(transaction__isnull=True, created_at__gte=start, created_at__lt=end))
    order_ids = list(orders.values_list('pk', flat=True))
    items = TransactionItem.objects.filter(transaction_id__in=ids)
    selected = defaultdict(set)
    for model, query in [(Transaction, receipts), (TransactionItem, items), (Order, orders),
                         (OrderItem, OrderItem.objects.filter(order_id__in=order_ids)),
                         (Product, Product.objects.filter(pk__in=items.values('product_id_snapshot'))),
                         (Category, Category.objects.filter(pk__in=items.values('category_id_snapshot')))]:
        selected[model].update(query.values_list('pk', flat=True))
    while True:
        added = 0
        for model, keys in list(selected.items()):
            if not keys:
                continue
            for field in model._meta.concrete_fields:
                if not field.is_relation:
                    continue
                target = field.remote_field.model
                references = set(model._base_manager.filter(pk__in=keys).exclude(
                    **{field.attname: None}).values_list(field.attname, flat=True))
                # Foreign keys in the dataset target primary keys.
                if field.target_field != target._meta.pk:
                    references = set(target._base_manager.filter(**{field.target_field.name+'__in': references}).values_list('pk', flat=True))
                missing = references-selected[target]
                selected[target].update(missing)
                added += len(missing)
        if not added:
            return {m: keys for m, keys in selected.items() if keys}


def fingerprints(db, selected):
    result = {}
    quote = db.ops.quote_name
    with db.cursor() as cursor:
        cursor.execute("SET TIME ZONE 'UTC'")
        for model in sorted(selected, key=lambda m:m._meta.db_table):
            columns = ', '.join(quote(f.column) for f in model._meta.concrete_fields)
            table, pk = quote(model._meta.db_table), quote(model._meta.pk.column)
            cursor.execute(f'SELECT row_to_json(t)::text FROM (SELECT {columns} FROM {table} WHERE {pk} = ANY(%s) ORDER BY {pk}) t',
                           [list(selected[model])])
            digest, count = sha256(), 0
            while rows := cursor.fetchmany(1000):
                for row in rows:
                    digest.update(row[0].encode());digest.update(b'\n');count += 1
            result[model._meta.db_table] = {'count': count, 'sha256': digest.hexdigest()}
    return result


def write_sql(path, selected, years):
    quote = connection.ops.quote_name
    with path.open('x',encoding='utf-8') as stream, connection.cursor() as cursor:
        path.chmod(0o600)
        stream.write(f'-- Rosario Dairy actual sales {years[0]}-{years[1]}; data-only PostgreSQL export.\n'
                     '-- Restore only into an EMPTY migrated application database of the recorded schema.\n'
                     '-- Includes referenced catalogue/account rows; excludes out-of-range receipts and dummy sales.\n'
                     "BEGIN;\nSET TIME ZONE 'UTC';\nSET CONSTRAINTS ALL DEFERRED;\n")
        for model in sorted(selected, key=lambda m:m._meta.db_table):
            fields = list(model._meta.concrete_fields)
            columns = ', '.join(quote(f.column) for f in fields)
            table = quote(model._meta.db_table)
            placeholders = '('+', '.join(['%s']*len(fields))+')'
            rows = []
            for obj in model._base_manager.filter(pk__in=selected[model]).order_by('pk').iterator(chunk_size=1000):
                values = [f.get_db_prep_value(getattr(obj,f.attname),connection,prepared=False) for f in fields]
                rows.append(cursor.mogrify(placeholders,values).decode(connection.connection.encoding))
                if len(rows)==250:
                    stream.write(f'INSERT INTO {table} ({columns}) VALUES\n'+',\n'.join(rows)+';\n');rows=[]
            if rows:
                stream.write(f'INSERT INTO {table} ({columns}) VALUES\n'+',\n'.join(rows)+';\n')
            pk = model._meta.pk.column
            sequence = cursor.mogrify('SELECT pg_get_serial_sequence(%s,%s)',[model._meta.db_table,pk]).decode()
            stream.write(f'SELECT setval(({sequence}), (SELECT MAX({quote(pk)}) FROM {table}), true);\n')
        stream.write('SET CONSTRAINTS ALL IMMEDIATE;\nCOMMIT;\n')


def verify_restore(path, selected, before):
    """Build only DDL in a new throwaway database; never run data migrations."""
    name = 'rosario_backup_verify_'+uuid4().hex[:12]
    alias = 'sales_backup_verification'
    created = False
    db = None
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        created = True
        config = deepcopy(connection.settings_dict)
        config['NAME'] = name
        # A standalone wrapper never registers/reconfigures a live Django alias.
        # DDL uses this connection directly; no application data migrations run.
        db = DatabaseWrapper(config, alias=alias)
        with db.schema_editor(atomic=False) as editor:
            for model in apps.get_models():
                if model._meta.managed and not model._meta.proxy and not model._meta.swapped:
                    editor.create_model(model)
        with db.cursor() as cursor:
            cursor.execute(path.read_text())
        after = fingerprints(db,selected)
        if after != before:
            raise ValueError('Restored rows do not match the source snapshot.')
        # Reject any accidentally exported receipt outside the requested ID set.
        with db.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM sales_transaction')
            if cursor.fetchone()[0] != len(selected[Transaction]):
                raise ValueError('Unexpected extra receipts in restore.')
        return {'status':'passed','fingerprints_match':True,'tables':after,
                'method':'isolated_database_with_current_model_schema', 'temporary_database_removed':True}
    finally:
        if db is not None:
            db.close()
        if created:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


def backup_history(folder, start_year=2023, end_year=2025):
    if connection.vendor != 'postgresql':
        raise ValueError('This SQL backup requires PostgreSQL.')
    if not 2000 <= start_year <= end_year < timezone.localdate().year:
        raise ValueError('Choose complete historical years, starting in 2000 or later.')
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=False,mode=0o700)
    path=folder/f'actual-sales-{start_year}-{end_year}.sql'
    manifest={'completed':False,'scope':'data_only_actual_sales_with_referenced_dependencies',
              'start_year':start_year,'end_year':end_year,'business_timezone':'Asia/Manila',
              'restore_requirement':'Empty database at current application schema; not a server/roles/schema backup.',
              'created_at':timezone.now().isoformat()}
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        selected=select_rows(start_year,end_year)
        if not selected.get(Transaction):
            raise ValueError('No historical receipts in the requested range.')
        manifest['tables']=fingerprints(connection,selected)
        with connection.cursor() as cursor:
            cursor.execute('SELECT app,name FROM django_migrations ORDER BY app,name')
            manifest['applied_migrations']=[list(row) for row in cursor.fetchall()]
        write_sql(path,selected,(start_year,end_year))
    manifest['sql_sha256']=sha256(path.read_bytes()).hexdigest()
    manifest_path=folder/'manifest.json'
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n');manifest_path.chmod(0o600)
    manifest['restore_verification']=verify_restore(path,selected,manifest['tables'])
    compressed=path.with_suffix('.sql.gz')
    with path.open('rb') as source, gzip.open(compressed,'wb') as target:
        import shutil
        shutil.copyfileobj(source,target)
    compressed.chmod(0o600)
    manifest['gzip_sha256']=sha256(compressed.read_bytes()).hexdigest()
    manifest['completed']=True
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
