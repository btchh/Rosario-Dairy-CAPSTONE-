import json
from django.core.management.base import BaseCommand, CommandError
from forecasting.prospective import issue_current, score_closed


class Command(BaseCommand):
    help = 'Save prospective rough chart predictions and score reviewed closed outcomes; no fitting or approval.'

    def handle(self, *args, **options):
        try:
            issues = issue_current()
            scores = score_closed()
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps({'created': bool(issues or scores), 'issues_created': issues,
                                     'scores_created': scores, 'production_approved': False}))
