from datetime import datetime
from decimal import Decimal
import gzip
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo
import json

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase,TransactionTestCase
from sales.models import Customer,Transaction
from sales.backups.history_sql import select_rows,backup_history


class HistoricalDatasetSelectionTests(TestCase):
    def test_scope_uses_business_years_preserves_unassigned_and_excludes_dummy(self):
        user=get_user_model().objects.create_user(username='sql-scope',email='sql-scope@example.test')
        chosen=[]
        examples=[(datetime(2022,12,31,23,59),None,False),
                  (datetime(2023,1,1),None,True),
                  (datetime(2025,12,31,23,59),None,True),
                  (datetime(2026,1,1),None,False),
                  (datetime(2025,6,1),'2026:000001',False)]
        for day,reference,included in examples:
            sale=Transaction.objects.create(handled_by=user,total_amount=100,source_reference=reference,
                source_sales_segment='unclassified',customer=None)
            Transaction.objects.filter(pk=sale.pk).update(created_at=day.replace(tzinfo=ZoneInfo('Asia/Manila')))
            if included:chosen.append(sale.pk)
        selected=select_rows(2023,2025)
        self.assertEqual(selected[Transaction],set(chosen))
        self.assertEqual(selected[type(user)],{user.pk})


class HistoricalSQLRoundtripTests(TransactionTestCase):
    def test_sql_roundtrip_handles_quotes_unicode_nulls_money_and_cleans_up_database(self):
        user=get_user_model().objects.create_user(username='sql-roundtrip',email='sql-roundtrip@example.test')
        customer=Customer.objects.create(name="O'Brien dairy — test",created_by=user)
        sale=Transaction.objects.create(handled_by=user,customer=customer,total_amount=Decimal('12345.67'),
            source_customer_label="O'Brien\nMilk \\ branch",source_invoice_number=None,
            source_note="Quoted 'value'; not SQL; unicode ₱",source_sales_segment='unclassified')
        Transaction.objects.filter(pk=sale.pk).update(created_at=datetime(2025,1,15,tzinfo=ZoneInfo('Asia/Manila')))
        before=Transaction.objects.values().get(pk=sale.pk)
        with connection.cursor() as cursor:
            cursor.execute("SELECT datname FROM pg_database WHERE datname LIKE 'rosario_backup_verify_%'")
            databases_before={row[0] for row in cursor.fetchall()}
        with TemporaryDirectory() as parent:
            folder=Path(parent)/'dataset'
            original_open = Path.open

            def windows_default_open(path, mode='r', buffering=-1, encoding=None, errors=None, newline=None):
                # Reproduce a Windows cp1252 default even on a UTF-8 Linux host.
                if 'b' not in mode and encoding in (None, 'locale'):
                    encoding = 'cp1252'
                return original_open(path, mode, buffering, encoding, errors, newline)

            with patch.object(Path, 'open', windows_default_open):
                result=backup_history(folder)
            self.assertTrue(result['completed'])
            self.assertEqual(result['restore_verification']['status'],'passed')
            self.assertEqual(result['tables']['sales_transaction']['count'],1)
            self.assertTrue((folder/'actual-sales-2023-2025.sql').exists())
            self.assertTrue((folder/'actual-sales-2023-2025.sql.gz').exists())
            sql_path = folder/'actual-sales-2023-2025.sql'
            self.assertIn("O'Brien dairy — test", sql_path.read_text(encoding='utf-8').replace("''", "'"))
            self.assertIn('unicode ₱', sql_path.read_text(encoding='utf-8'))
            self.assertEqual(gzip.decompress((folder/'actual-sales-2023-2025.sql.gz').read_bytes()),
                             sql_path.read_bytes())
            if os.name == 'posix':
                self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
                for filename in ('actual-sales-2023-2025.sql', 'actual-sales-2023-2025.sql.gz', 'manifest.json'):
                    self.assertEqual((folder/filename).stat().st_mode & 0o777, 0o600)
            self.assertTrue(json.loads((folder/'manifest.json').read_text())['completed'])
        self.assertEqual(Transaction.objects.values().get(pk=sale.pk),before)
        with connection.cursor() as cursor:
            cursor.execute("SELECT datname FROM pg_database WHERE datname LIKE 'rosario_backup_verify_%'")
            self.assertEqual({row[0] for row in cursor.fetchall()},databases_before)
