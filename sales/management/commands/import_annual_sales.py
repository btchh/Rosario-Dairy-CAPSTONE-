import json
import hashlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from sales.importers.annual_sales import parse_workbooks
from sales.importers.load_annual_sales import load


class Command(BaseCommand):
    help = 'Audit or atomically import the three annual sales workbooks.'

    def add_arguments(self, parser):
        parser.add_argument('source_dir', type=Path)
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--admin-username')
        parser.add_argument('--audit-path', type=Path)

    def handle(self, *args, **options):
        try:
            result = parse_workbooks(options['source_dir'])
            summary = result.summary()
            audit = {
                'summary': summary, 'issues': result.issues,
                'invoice_checks': result.daily_checks,
                'monthly_checks': result.monthly_checks,
                'source_files': [
                    {'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                    for path in sorted(options['source_dir'].glob('SALES REPORT JAN-DEC *.xlsx'))
                ],
            }
            if options['audit_path']:
                options['audit_path'].write_text(json.dumps(audit, indent=2) + '\n')
            self.stdout.write(json.dumps(summary, indent=2))
            if not options['apply']:
                return
            if not options['admin_username']:
                raise CommandError('--admin-username is required with --apply.')
            loaded = load(result, options['admin_username'])
            self.stdout.write(self.style.SUCCESS(json.dumps(loaded, indent=2)))
        except (ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc
