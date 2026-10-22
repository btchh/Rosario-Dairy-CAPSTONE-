from django.core.management.base import BaseCommand, CommandError
from forecasting.lifecycle import activate_model
from forecasting.models import MonthlyModelRun


class Command(BaseCommand):
    help = 'Activate or roll back to a saved current-source model only if the same publication gates pass.'

    def add_arguments(self, parser):
        parser.add_argument('model_id', type=int)

    def handle(self, *args, **options):
        try:
            run, changed = activate_model(options['model_id'])
        except (ValueError, MonthlyModelRun.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'Current approved monthly model: {run.pk}; changed={changed}')
