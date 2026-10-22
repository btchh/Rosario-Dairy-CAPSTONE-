from datetime import date
from django.core.management.base import BaseCommand, CommandError
from accounts.models import Users
from forecasting.lifecycle import attest_coverage


class Command(BaseCommand):
    help = 'Record an admin source reconciliation for one completed month; never infer completeness from invoice counts.'

    def add_arguments(self, parser):
        parser.add_argument('month', type=date.fromisoformat, help='First day of the reviewed month, YYYY-MM-01.')
        parser.add_argument('--admin-username', required=True)
        parser.add_argument('--note', required=True, help='Reconciliation evidence, including held/excluded source rows.')
        parser.add_argument('--confirm-zero', action='store_true')

    def handle(self, *args, **options):
        try:
            reviewed, created = attest_coverage(options['month'], options['admin_username'],
                                                options['note'], options['confirm_zero'])
        except (ValueError, Users.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'Coverage {reviewed.pk}: {reviewed.month}; created={created}; source={reviewed.source_revision}')
