import json
from datetime import date
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from forecasting.prospective import attest, PERIODS, SEGMENTS


class Command(BaseCommand):
    help = 'Reconcile a completed regular or feeding period before prospective scoring; combined requires both.'

    def add_arguments(self, parser):
        parser.add_argument('period', choices=PERIODS)
        parser.add_argument('segment', choices=SEGMENTS)
        parser.add_argument('target', type=date.fromisoformat)
        parser.add_argument('--admin-username', required=True)
        parser.add_argument('--note', required=True)
        parser.add_argument('--confirm-zero', action='store_true')

    def handle(self, *args, **options):
        try:
            review, created = attest(options['period'], options['segment'], options['target'],
                options['admin_username'], options['note'], options['confirm_zero'])
        except (ValueError, get_user_model().DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps({'coverage_id': review.pk, 'created': created,
                                     'confirmed_zero': review.confirmed_zero}))
