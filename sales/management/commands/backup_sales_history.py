import json
from pathlib import Path
from django.core.management.base import BaseCommand,CommandError
from sales.backups.history_sql import backup_history


class Command(BaseCommand):
    help = 'Export complete historical sales years to SQL and verify restoration in a temporary database.'

    def add_arguments(self,parser):
        parser.add_argument('--start-year',type=int,default=2023)
        parser.add_argument('--end-year',type=int,default=2025)
        parser.add_argument('--output',type=Path,required=True)

    def handle(self,*args,**options):
        try:
            result=backup_history(options['output'],options['start_year'],options['end_year'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps({'completed':result['completed'],'output':str(options['output']),
            'tables':{k:v['count'] for k,v in result['tables'].items()},
            'restore_verification':result['restore_verification']['status']}))
