"""Prospective evidence for rough chart projections, independent of approval.

Only forecasts saved before target start qualify. Unknown outcomes are never
zero-filled. Reviews bind to exact segment rows and are invalidated by corrections.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .data import load_snapshot
from .learning import shift_month
from .lifecycle import _commit_lock
from .models import ProjectionCoverage, ProjectionIssue, ProjectionScore, ProvisionalForecastRun
from .provisional import PERIODS, VERSION, VIEWS, context, digest, series

SEGMENTS = ('regular', 'feeding')
BASELINE = 'last_observed_component_period; yearly=12*last_observed_month'


def boundary(day, period):
    if period not in PERIODS or not isinstance(day, date) or isinstance(day, datetime):
        raise ValueError('Use a valid business date and weekly, monthly or yearly period.')
    if period == 'weekly':
        return day-timedelta(days=day.weekday())
    return day.replace(day=1, month=1) if period == 'yearly' else day.replace(day=1)


def period_end(target, period):
    return (target+timedelta(days=7) if period == 'weekly' else
            shift_month(target, 12 if period == 'yearly' else 1))


def validate_target(target, period):
    if boundary(target, period) != target:
        raise ValueError('Target must be the first business date of the period (Monday for weeks).')


def outcome(snapshot, period, target, segment):
    """Include unresolved entries in fingerprints: classification changes undo review."""
    end = period_end(target, period)
    rows = [s for s in snapshot.sales if target <= s.day < end and s.segment in (segment, 'unclassified')]
    revision = digest([(s.id, s.day.isoformat(), s.customer_key, s.customer_name, s.segment, str(s.revenue))
                       for s in sorted(rows, key=lambda s: s.id)])
    selected = [s for s in rows if s.segment == segment]
    return {'revision': revision, 'actual': sum((s.revenue for s in selected), Decimal('0')),
            'empty': not selected, 'unresolved': any(s.segment == 'unclassified' and s.revenue != 0 for s in rows)}


def attest(period, segment, target, username, note, confirm_zero=False):
    validate_target(target, period)
    if segment not in SEGMENTS or not note.strip():
        raise ValueError('Review regular or feeding separately and provide a reconciliation note.')
    today = timezone.localdate()
    if period_end(target, period) > today:
        raise ValueError('Only completed periods can be reviewed.')
    reviewer = get_user_model().objects.get(username=username, role='admin', is_active=True)
    with transaction.atomic():
        _commit_lock()
        result = outcome(load_snapshot(today), period, target, segment)
        if result['unresolved']:
            raise ValueError('Resolve nonzero unclassified entries before confirming coverage.')
        if result['empty'] and not confirm_zero:
            raise ValueError('No observations; --confirm-zero is required after reconciling a true zero period.')
        return ProjectionCoverage.objects.get_or_create(
            period=period, segment=segment, target=target, actual_revision=result['revision'],
            defaults={'confirmed_zero': result['empty'], 'reviewed_by': reviewer, 'note': note.strip()})


def baseline(snapshot, view, period):
    values = []
    for segment in SEGMENTS if view == 'combined' else (view,):
        rows = series(snapshot, segment, 'monthly' if period == 'yearly' else period)
        if not rows:
            return None
        values.append(Decimal(str(rows[-1]['actual']))*(12 if period == 'yearly' else 1))
    return sum(values, Decimal('0'))


def issue_current():
    """First origin prediction wins, including after a same-origin source correction."""
    with transaction.atomic():
        _commit_lock()
        now = timezone.now()
        today = timezone.localdate(now)
        snapshots, targets, revision = context(today)
        run = ProvisionalForecastRun.objects.filter(engine_version=VERSION, source_revision=revision).first()
        if run is None:
            return 0
        findings = dict(run.findings)
        checksum = findings.pop('artifact_digest', None)
        if checksum != digest(findings) or run.created_at > now:
            raise ValueError('Cannot issue a corrupted or future-dated projection artifact.')
        if findings['targets'] != targets or findings['source_revisions'] != {p: s.revision for p, s in snapshots.items()}:
            raise ValueError('Projection evidence does not match current inputs and targets.')
        created = 0
        for period in PERIODS:
            origin = boundary(today, period)
            for view in VIEWS:
                result = findings['periods'][period][view]
                reference = baseline(snapshots['monthly' if period == 'yearly' else period], view, period)
                for horizon, point in enumerate(result['points'], 1):
                    target = date.fromisoformat(point['date'])
                    validate_target(target, period)
                    if target <= today or point['date'] not in targets[period]:
                        raise ValueError('Only future target periods can be issued prospectively.')
                    _, added = ProjectionIssue.objects.get_or_create(
                        engine_version=run.engine_version, period=period, view=view, origin=origin, target=target,
                        defaults={'run': run, 'horizon': horizon, 'value': Decimal(str(point['predicted'])),
                            'lower_bound': Decimal(str(point['lower_bound'])),
                            'upper_bound': Decimal(str(point['upper_bound'])),
                            'baseline_value': reference,
                            'evidence': {'artifact_digest': checksum, 'source_revisions': findings['source_revisions'],
                                'baseline_method': BASELINE, 'basis': point['basis'],
                                'interval_basis': point['interval_basis'], 'production_approved': False}})
                    created += added
        if timezone.localdate() != today:
            raise ValueError('Business date changed while issuing; retry with current targets.')
        return created


def reviewed_outcome(snapshot, issued, coverage, actual_cache):
    if period_end(issued.target, issued.period) > snapshot.as_of:
        return None
    # Database/import timestamps must prove a real issue before the target began.
    issued_day = timezone.localdate(issued.issued_at)
    if (issued_day >= issued.target or boundary(issued_day, issued.period) != issued.origin or
            issued.run.created_at > issued.issued_at):
        return None
    results = []
    for segment in SEGMENTS if issued.view == 'combined' else (issued.view,):
        key = (issued.period, issued.target, segment)
        if key not in actual_cache:
            actual_cache[key] = outcome(snapshot, issued.period, issued.target, segment)
        result = actual_cache[key]
        review = coverage.get((issued.period, segment, issued.target, result['revision']))
        if result['unresolved'] or review is None or result['empty'] and not review.confirmed_zero:
            return None
        results.append((segment, result, review.pk))
    actual = sum((r['actual'] for _, r, _ in results), Decimal('0'))
    revision = digest([(s, r['revision'], pk) for s, r, pk in results])
    return actual, revision


def score_closed():
    created = 0
    with transaction.atomic():
        _commit_lock()
        snapshot = load_snapshot(timezone.localdate())
        coverage = {(c.period, c.segment, c.target, c.actual_revision): c for c in ProjectionCoverage.objects.all()}
        actual_cache = {}
        for issued in ProjectionIssue.objects.select_related('run').defer('run__findings').filter(target__lt=snapshot.as_of).iterator():
            result = reviewed_outcome(snapshot, issued, coverage, actual_cache)
            if result is None:
                continue
            actual, revision = result
            error = issued.value-actual
            _, added = ProjectionScore.objects.get_or_create(issued=issued, actual_revision=revision,
                defaults={'actual': actual, 'error': error, 'absolute_error': abs(error),
                    'baseline_absolute_error': abs(issued.baseline_value-actual) if issued.baseline_value is not None else None,
                    'within_bounds': issued.lower_bound <= actual <= issued.upper_bound})
            created += added
    return created


def evidence_report():
    """Read-only; invalidate corrected outcomes immediately, even before maintenance."""
    snapshot = load_snapshot(timezone.localdate())
    coverage = {(c.period, c.segment, c.target, c.actual_revision): c for c in ProjectionCoverage.objects.all()}
    scores = {(s.issued_id, s.actual_revision): s for s in ProjectionScore.objects.all()}
    groups = {}
    actual_cache = {}
    for issued in ProjectionIssue.objects.select_related('run').defer('run__findings').order_by('pk').iterator():
        key = (issued.engine_version, issued.period, issued.view, issued.horizon)
        group = groups.setdefault(key, {'issued_count': 0, 'awaiting_outcome_count': 0,
                                       'awaiting_review_or_scoring_count': 0, 'scores': []})
        group['issued_count'] += 1
        if period_end(issued.target, issued.period) > snapshot.as_of:
            group['awaiting_outcome_count'] += 1
            continue
        result = reviewed_outcome(snapshot, issued, coverage, actual_cache)
        score = scores.get((issued.pk, result[1])) if result else None
        if score is None:
            group['awaiting_review_or_scoring_count'] += 1
        else:
            group['scores'].append(score)
    rows = []
    for (engine, period, view, horizon), group in sorted(groups.items()):
        points = group.pop('scores')
        actual = sum((abs(s.actual) for s in points), Decimal('0'))
        paired = [s for s in points if s.baseline_absolute_error is not None]
        paired_actual = sum((abs(s.actual) for s in paired), Decimal('0'))
        def wape(errors, denominator):
            return float(sum(errors, Decimal('0'))/denominator*100) if denominator else None
        rows.append({'engine_version': engine, 'period': period, 'view': view, 'horizon': horizon, **group,
            'scored_count': len(points), 'wape': wape((s.absolute_error for s in points), actual),
            'mae': float(sum((s.absolute_error for s in points), Decimal('0'))/len(points)) if points else None,
            'bias': float(sum((s.error for s in points), Decimal('0'))/len(points)) if points else None,
            'baseline_paired_count': len(paired),
            'baseline_wape': wape((s.baseline_absolute_error for s in paired), paired_actual),
            'model_paired_wape': wape((s.absolute_error for s in paired), paired_actual),
            'nominal_interval_coverage_percent': sum(s.within_bounds for s in points)/len(points)*100 if points else None})
    return {'status': 'collecting_prospective_evidence', 'production_approved': False,
            'as_of': snapshot.as_of.isoformat(), 'baseline_method': BASELINE, 'groups': rows,
            'notes': ['Only predictions saved before target start and reviewed completed actuals are scored.',
                      'Origins and horizons are kept separate; overlapping long forecasts are correlated.',
                      'Bounds are uncalibrated; yearly and combined bounds are sums, not joint intervals.',
                      'This tracks provisional chart models, independently of primary regular-monthly approval.']}
