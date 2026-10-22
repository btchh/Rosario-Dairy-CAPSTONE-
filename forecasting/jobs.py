"""Incremental, versioned research refresh. No legacy model fitting or publication."""
from django.db import transaction
from .data import load_snapshot
from .models import PatternStudy
from .patterns import ANALYZER_VERSION, investigate


def refresh_patterns(as_of=None):
    snapshot=load_snapshot(as_of)
    existing=PatternStudy.objects.filter(analyzer_version=ANALYZER_VERSION,
                    source_revision=snapshot.revision,as_of=snapshot.as_of).first()
    if existing:return existing,False
    findings=investigate(snapshot)
    if load_snapshot(snapshot.as_of).revision!=snapshot.revision:
        raise ValueError('Sales changed during investigation; retry the refresh.')
    with transaction.atomic():
        study,created=PatternStudy.objects.get_or_create(
            analyzer_version=ANALYZER_VERSION,source_revision=snapshot.revision,as_of=snapshot.as_of,
            defaults={'observed_through':findings['observed_through'],'findings':findings})
    return study,created
