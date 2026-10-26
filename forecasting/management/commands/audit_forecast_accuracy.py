import json

from django.core.management.base import BaseCommand
from forecasting.accuracy import accuracy_report


class Command(BaseCommand):
    help = 'Read current real-sales SARIMA accuracy, baseline skill, error dispersion and evidence gaps. Never fits.'

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(accuracy_report(), allow_nan=False, default=str))
