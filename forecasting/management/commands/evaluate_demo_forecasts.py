"""Retained command name fails explicitly instead of using synthetic sales."""
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from forecasting.demo import DISABLED_MESSAGE


class Command(BaseCommand):
    help = 'Disabled: forecasting uses recorded actual invoices only.'

    def add_arguments(self, parser):
        parser.add_argument('--output', type=Path)

    def handle(self, *args, **options):
        raise CommandError(DISABLED_MESSAGE)
